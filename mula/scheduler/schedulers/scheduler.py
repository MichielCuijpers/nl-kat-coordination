import abc
import logging
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from scheduler import connectors, context, models, queues, utils
from scheduler.utils import thread


class Scheduler(abc.ABC):
    """The Scheduler class combines the priority queue, and ranker.
    The scheduler is responsible for populating the queue, and ranking tasks.

    An implementation of the Scheduler will likely implement the
    `populate_queue` method, with the strategy for populating the queue. By
    extending this you can create your own rules of what items should be
    ranked and put onto the priority queue.

    Attributes:
        logger:
            The logger for the class
        ctx:
            Application context of shared data (e.g. configuration, external
            services connections).
        scheduler_id:
            The id of the scheduler.
        queue:
            A queues.PriorityQueue instance
        ranker:
            A rankers.Ranker instance.
        populate_queue_enabled:
            A boolean whether to populate the queue.
        threads:
            A dict of ThreadRunner instances, used for runner processes
            concurrently.
        stop_event: A threading.Event object used for communicating a stop
            event across threads.
        listeners:
            A dict of connector.Listener instances.
    """

    organisation: models.Organisation

    def __init__(
        self,
        ctx: context.AppContext,
        scheduler_id: str,
        queue: queues.PriorityQueue = None,
        callback: Optional[Callable[..., None]] = None,
        populate_queue_enabled: bool = True,
        max_tries: int = -1,
    ):
        """Initialize the Scheduler.

        Args:
            ctx:
                Application context of shared data (e.g. configuration, external
                services connections).
            scheduler_id:
                The id of the scheduler.
            queue:
                A queues.PriorityQueue instance
            ranker:
                A rankers.Ranker instance.
            populate_queue_enabled:
                A boolean whether to populate the queue.
            max_tries:
                The maximum number of retries for a task to be pushed to
                the queue.
        """

        self.logger: logging.Logger = logging.getLogger(__name__)
        self.ctx: context.AppContext = ctx
        self.scheduler_id = scheduler_id

        self.queue: queues.PriorityQueue = queue

        self.populate_queue_enabled: bool = populate_queue_enabled
        self.max_tries: int = max_tries

        self.callback: Optional[Callable[[], Any]] = callback
        self.threads: List[thread.ThreadRunner] = []
        self.stop_event: threading.Event = threading.Event()

        self.listeners: Dict[str, connectors.listeners.Listener] = {}

    @abc.abstractmethod
    def run(self) -> None:
        raise NotImplementedError

    def handle_signal_task_updated(self, task: models.Task) -> None:
        pass

    def post_push(self, p_item: models.PrioritizedItem) -> None:
        """When a boefje task is being added to the queue. We
        persist a task to the datastore with the status QUEUED

        Args:
            p_item: The prioritized item to post-add to queue.
        """
        # NOTE: we set the id of the task the same as the p_item, for easier
        # lookup.
        task = models.Task(
            id=p_item.id,
            scheduler_id=self.scheduler_id,
            type=self.queue.item_type.type,
            p_item=p_item,
            status=models.TaskStatus.QUEUED,
            created_at=datetime.now(timezone.utc),
            modified_at=datetime.now(timezone.utc),
        )

        if p_item.hash is None:
            self.logger.warning(
                "Task %s has no hash, not creating job [task_id=%s, queue_id=%s]",
                p_item.data.get("id"),
                p_item.data.get("id"),
                self.queue.pq_id,
            )
            return

        task_db = self.ctx.task_store.get_task_by_id(str(p_item.id))
        if task_db is not None:
            self.ctx.task_store.update_task(task)
            return

        task_db = self.ctx.task_store.create_task(task)
        if task_db is None:
            self.logger.warning(
                "Task %s could not be created, not creating job [task_id=%s, queue_id=%s]",
                p_item.data.get("id"),
                p_item.data.get("id"),
                self.queue.pq_id,
            )
            return

        # When there is already a job with the same hash, we update the job
        # so that it can be evaluated again.
        job_db = self.ctx.job_store.get_job_by_hash(p_item.hash)
        if job_db is None:
            try:
                job_db = self.ctx.job_store.create_job(
                    models.Job(
                        scheduler_id=self.scheduler_id,
                        hash=p_item.hash,
                        enabled=True,
                        p_item=p_item,
                        created_at=datetime.now(timezone.utc),
                        modified_at=datetime.now(timezone.utc),
                    )
                )
            except Exception as e:
                self.logger.warning(
                    "Job %s could not be created [task_id=%s, queue_id=%s]: %s",
                    p_item.data.get("id"),
                    p_item.data.get("id"),
                    self.queue.pq_id,
                    e,
                )
                return

        # Update job, if was already disabled, we enable it again.
        if not job_db.enabled:
            job_db.enabled = True

        try:
            self.ctx.job_store.update_job(job_db)  # TODO: test this modified_at
        except Exception as e:
            self.logger.warning(
                "Job %s could not be updated [task_id=%s, queue_id=%s]: %s",
                job_db.id,
                p_item.data.get("id"),
                self.queue.pq_id,
                e,
            )
            return

        # Update task: For the task create the relationship with the associated job
        task_db.job_id = job_db.id

        try:
            self.ctx.task_store.update_task(task_db)
        except Exception as e:
            self.logger.warning(
                "Task %s could not be updated [task_id=%s, queue_id=%s]: %s",
                task_db.id,
                p_item.data.get("id"),
                self.queue.pq_id,
                e,
            )

        return

    def post_pop(self, p_item: models.PrioritizedItem) -> None:
        """When a boefje task is being removed from the queue. We
        persist a task to the datastore with the status RUNNING

        Args:
            p_item: The prioritized item to post-pop from queue.
        """
        # NOTE: we set the id of the task the same as the p_item, for easier
        # lookup.
        task = self.ctx.task_store.get_task_by_id(str(p_item.id))
        if task is None:
            self.logger.warning(
                "Task %s not found in datastore, not updating status [task_id=%s, queue_id=%s]",
                p_item.data.get("id"),
                p_item.data.get("id"),
                self.queue.pq_id,
            )
            return None

        task.status = models.TaskStatus.DISPATCHED
        self.ctx.task_store.update_task(task)

        return None

    def pop_item_from_queue(self, filters: Optional[List[models.Filter]] = None) -> Optional[models.PrioritizedItem]:
        """Pop an item from the queue.

        Returns:
            A PrioritizedItem instance.
        """
        try:
            p_item = self.queue.pop(filters)
        except queues.QueueEmptyError as exc:
            raise exc

        if p_item is not None:
            self.post_pop(p_item)

        return p_item

    def push_item_to_queue(self, p_item: models.PrioritizedItem) -> None:
        """Push an item to the queue.

        Args:
            item: The item to push to the queue.
        """
        try:
            self.queue.push(p_item)
        except queues.errors.NotAllowedError as exc:
            self.logger.warning(
                "Not allowed to push to queue %s [queue_id=%s, qsize=%d]",
                self.queue.pq_id,
                self.queue.pq_id,
                self.queue.qsize(),
            )
            raise exc
        except queues.errors.QueueFullError as exc:
            self.logger.warning(
                "Queue %s is full, not populating new tasks [queue_id=%s, qsize=%d]",
                self.queue.pq_id,
                self.queue.pq_id,
                self.queue.qsize(),
            )
            raise exc
        except queues.errors.InvalidPrioritizedItemError as exc:
            self.logger.warning(
                "Invalid prioritized item %s [queue_id=%s, qsize=%d]",
                p_item,
                self.queue.pq_id,
                self.queue.qsize(),
            )
            raise exc

        self.logger.debug(
            "Pushed item (%s) to queue %s with priority %s "
            "[p_item.id=%s, p_item.hash=%s, queue.pq_id=%s, queue.qsize=%d]",
            p_item.id,
            self.queue.pq_id,
            p_item.priority,
            p_item.id,
            p_item.hash,
            self.queue.pq_id,
            self.queue.qsize(),
        )

        self.post_push(p_item)

    def push_items_to_queue(self, p_items: List[models.PrioritizedItem]) -> None:
        """Add items to a priority queue.

        Args:
            pq: The priority queue to add items to.
            items: The items to add to the queue.
        """
        count = 0
        for p_item in p_items:
            try:
                self.push_item_to_queue(p_item)
            except (
                queues.errors.NotAllowedError,
                queues.errors.QueueFullError,
                queues.errors.InvalidPrioritizedItemError,
            ):
                self.logger.debug(
                    "Unable to push item to queue %s [queue_id=%s, qsize=%d, item=%s, exc=%s]",
                    self.queue.pq_id,
                    self.queue.pq_id,
                    self.queue.qsize(),
                    p_item,
                    traceback.format_exc(),
                )
                continue
            except Exception as exc:
                self.logger.error(
                    "Unable to push item to queue %s [queue_id=%s, qsize=%d, item=%s]",
                    self.queue.pq_id,
                    self.queue.pq_id,
                    self.queue.qsize(),
                    p_item,
                )
                raise exc

            count += 1

    def push_item_to_queue_with_timeout(
        self,
        p_item: models.PrioritizedItem,
        max_tries: int = 5,
        timeout: int = 1,
    ) -> None:
        """Push an item to the queue, with a timeout.

        Args:
            p_item: The item to push to the queue.
            timeout: The timeout in seconds.
            max_tries: The maximum number of tries. Set to -1 for infinite tries.

        Raises:
            QueueFullError: When the queue is full.
        """
        tries = 0
        while not self.is_space_on_queue() and (tries < max_tries or max_tries == -1):
            self.logger.debug(
                "Queue %s is full, waiting for space [queue_id=%s, qsize=%d]",
                self.queue.pq_id,
                self.queue.pq_id,
                self.queue.qsize(),
            )
            time.sleep(timeout)
            tries += 1

        if tries >= max_tries and max_tries != -1:
            raise queues.errors.QueueFullError()

        self.push_item_to_queue(p_item)

    def run_in_thread(
        self,
        name: str,
        target: Callable[[], Any],
        interval: float = 0.01,
        daemon: bool = False,
        loop: bool = True,
    ) -> None:
        """Make a function run in a thread, and add it to the dict of threads.

        Args:
            name: The name of the thread.
            func: The function to run in the thread.
            interval: The interval to run the function.
            daemon: Whether the thread should be a daemon.
        """
        t = utils.ThreadRunner(
            name=name,
            target=target,
            stop_event=self.stop_event,
            interval=interval,
            daemon=daemon,
            loop=loop,
        )
        t.start()

        self.threads.append(t)

    def stop(self) -> None:
        """Stop the scheduler."""
        self.logger.info("Stopping scheduler: %s", self.scheduler_id)

        # First stop the listeners, when those are running in a thread and
        # they're using rabbitmq, they will block. Setting the stop event
        # will not stop the thread. We need to explicitly stop the listener.
        for lst in self.listeners.values():
            lst.stop()

        for t in self.threads:
            t.join(5)

        if self.callback:
            self.callback(self.scheduler_id)  # type: ignore [call-arg]

        self.logger.info("Stopped scheduler: %s", self.scheduler_id)

    def is_space_on_queue(self) -> bool:
        """Check if there is space on the queue.

        NOTE: maxsize 0 means unlimited
        """
        if (self.queue.maxsize - self.queue.qsize()) <= 0 and self.queue.maxsize != 0:
            return False

        return True

    def is_item_on_queue_by_hash(self, item_hash: str) -> bool:
        return self.queue.is_item_on_queue_by_hash(item_hash)

    def is_alive(self) -> bool:
        """Check if the scheduler is alive."""
        return not self.stop_event.is_set()

    def dict(self) -> Dict[str, Any]:
        return {
            "id": self.scheduler_id,
            "populate_queue_enabled": self.populate_queue_enabled,
            "priority_queue": {
                "id": self.queue.pq_id,
                "item_type": self.queue.item_type.type,
                "maxsize": self.queue.maxsize,
                "qsize": self.queue.qsize(),
                "allow_replace": self.queue.allow_replace,
                "allow_updates": self.queue.allow_updates,
                "allow_priority_updates": self.queue.allow_priority_updates,
            },
        }
