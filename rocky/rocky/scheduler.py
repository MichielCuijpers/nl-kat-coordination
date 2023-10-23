from __future__ import annotations

import datetime
import uuid
from enum import Enum
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Union

import requests
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpRequest
from django.utils.translation import gettext_lazy as _
from pydantic import BaseModel, Field
from requests.exceptions import ConnectionError, HTTPError

from rocky.health import ServiceHealth


class Boefje(BaseModel):
    """Boefje representation."""

    id: str
    name: Optional[str] = Field(default=None)
    version: Optional[str] = Field(default=None)


class BoefjeMeta(BaseModel):
    """BoefjeMeta is the response object returned by the Bytes API"""

    id: uuid.UUID
    boefje: Boefje
    input_ooi: Optional[str]
    arguments: Dict[str, Any]
    organization: str
    started_at: Optional[datetime.datetime]
    ended_at: Optional[datetime.datetime]


class RawData(BaseModel):
    id: uuid.UUID
    boefje_meta: BoefjeMeta
    mime_types: List[Dict[str, str]]
    secure_hash: Optional[str]
    hash_retrieval_link: Optional[str]


class Normalizer(BaseModel):
    """Normalizer representation."""

    id: Optional[str]
    name: Optional[str]
    version: Optional[str] = Field(default=None)


class NormalizerMeta(BaseModel):
    id: uuid.UUID
    raw_data: RawData
    normalizer: Normalizer
    started_at: datetime.datetime
    ended_at: datetime.datetime


class NormalizerTask(BaseModel):
    """NormalizerTask represent data needed for a Normalizer to run."""

    id: uuid.UUID
    normalizer: Normalizer
    raw_data: RawData
    type: str = "normalizer"


class BoefjeTask(BaseModel):
    """BoefjeTask represent data needed for a Boefje to run."""

    id: uuid.UUID
    boefje: Boefje
    input_ooi: Optional[str]
    organization: str
    type: str = "boefje"


class QueuePrioritizedItem(BaseModel):
    """Representation of a queue.PrioritizedItem on the priority queue. Used
    for unmarshalling of priority queue prioritized items to a JSON
    representation.
    """

    id: uuid.UUID
    priority: int
    hash: Optional[str]
    data: Union[BoefjeTask, NormalizerTask]


class TaskStatus(Enum):
    """Status of a task."""

    PENDING = "pending"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    id: uuid.UUID
    scheduler_id: str
    type: str
    p_item: QueuePrioritizedItem
    status: TaskStatus
    created_at: datetime.datetime
    modified_at: datetime.datetime

    class Config:
        orm_mode = True


class PaginatedTasksResponse(BaseModel):
    count: int
    next: Optional[str]
    previous: Optional[str]
    results: List[Task]


class LazyTaskList:
    def __init__(
        self,
        scheduler_client: SchedulerClient,
        **kwargs,
    ):
        self.scheduler_client = scheduler_client
        self.kwargs = kwargs
        self._count = None

    @property
    def count(self) -> int:
        if self._count is None:
            self._count = self.scheduler_client.list_tasks(
                limit=0,
                **self.kwargs,
            ).count
        return self._count

    def __len__(self):
        return self.count

    def __getitem__(self, key) -> List[Task]:
        if isinstance(key, slice):
            offset = key.start or 0
            limit = key.stop - offset
        elif isinstance(key, int):
            offset = key
            limit = 1
        else:
            raise TypeError("Invalid slice argument type.")

        res = self.scheduler_client.list_tasks(
            limit=limit,
            offset=offset,
            **self.kwargs,
        )

        self._count = res.count
        return res.results


class SchedulerError(Exception):
    message = _("Connectivity issues with Mula.")

    def __str__(self):
        return str(self.message)


class TooManyRequestsError(SchedulerError):
    message = _("Task queue is full, please try again later.")


class BadRequestError(SchedulerError):
    message = _("Task is invalid.")


class ConflictError(SchedulerError):
    message = _("Task already queued.")


class TaskNotFoundError(SchedulerError):
    message = _("Task could not be found.")


class SchedulerClient:
    def __init__(self, base_uri: str):
        self.session = requests.Session()
        self._base_uri = base_uri

    def list_tasks(
        self,
        **kwargs,
    ) -> PaginatedTasksResponse:
        try:
            res = self.session.get(f"{self._base_uri}/tasks", params=kwargs)
            return PaginatedTasksResponse.parse_raw(res.text)
        except ConnectionError:
            raise SchedulerError()

    def get_lazy_task_list(
        self,
        scheduler_id: str,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        min_created_at: Optional[datetime.datetime] = None,
        max_created_at: Optional[datetime.datetime] = None,
        input_ooi: Optional[str] = None,
        plugin_id: Optional[str] = None,
        boefje_name: Optional[str] = None,
    ) -> LazyTaskList:
        try:
            return LazyTaskList(
                self,
                scheduler_id=scheduler_id,
                type=task_type,
                status=status,
                min_created_at=min_created_at,
                max_created_at=max_created_at,
                input_ooi=input_ooi,
                plugin_id=plugin_id,
                boefje_name=boefje_name,
            )
        except ConnectionError:
            raise SchedulerError()

    def get_task_details(self, task_id) -> Task | None:
        try:
            res = self.session.get(f"{self._base_uri}/tasks/{task_id}")
            res.raise_for_status()
            return Task.parse_raw(res.content)
        except HTTPError:
            raise Http404()
        except ConnectionError:
            raise SchedulerError()

    def push_task(self, queue_name: str, prioritized_item: QueuePrioritizedItem) -> None:
        try:
            res = self.session.post(f"{self._base_uri}/queues/{queue_name}/push", data=prioritized_item.json())
            res.raise_for_status()
        except HTTPError as http_error:
            code = http_error.response.status_code
            if code == HTTPStatus.TOO_MANY_REQUESTS:
                raise TooManyRequestsError()
            elif code == HTTPStatus.BAD_REQUEST:
                raise BadRequestError()
            elif code == HTTPStatus.CONFLICT:
                raise ConflictError()
            else:
                raise SchedulerError()

    def health(self) -> ServiceHealth:
        try:
            health_endpoint = self.session.get(f"{self._base_uri}/health")
            health_endpoint.raise_for_status()
            return ServiceHealth.parse_raw(health_endpoint.content)
        except ConnectionError:
            raise SchedulerError()


def get_scheduler_client():
    try:
        client = SchedulerClient(settings.SCHEDULER_API)
        client.health()
        return client
    except ConnectionError:
        raise SchedulerError()


def schedule_task(request: HttpRequest, organization_code: str, task: QueuePrioritizedItem) -> None:
    plugin_name = ""
    input_ooi = ""
    if task.data.type == "boefje":
        plugin_name = task.data.boefje.name
        input_ooi = task.data.input_ooi
    if task.data.type == "normalizer":
        plugin_name = task.data.normalizer.id  # name not set yet, is None for name
        input_ooi = task.data.raw_data.boefje_meta.input_ooi
    try:
        get_scheduler_client().push_task(f"{task.data.type}-{organization_code}", task)
    except (BadRequestError, TooManyRequestsError, ConflictError) as task_error:
        error_message = task_error.message + _(" Scheduling {} {} with input object {} failed.").format(
            task.data.type.title(), plugin_name, input_ooi
        )
        messages.error(request, error_message)
    except SchedulerError as error:
        messages.error(request, error.message)
    else:
        messages.success(
            request,
            _(
                "Task '{} {} with input object {}' is scheduled and will soon be started in the background. "
                "Results will be added to the object list when they are in. "
                "It may take some time, a refresh of the page may be needed to show the results."
            ).format(task.data.type.title(), plugin_name, input_ooi),
        )


def get_list_of_tasks_lazy(request: HttpRequest, **params) -> LazyTaskList | None:
    try:
        return get_scheduler_client().get_lazy_task_list(**params)
    except SchedulerError as error:
        messages.error(request, error.message)
        return []


def get_details_of_task(request: HttpRequest, task_id: str | None) -> Task | None:
    try:
        return get_scheduler_client().get_task_details(task_id)
    except (BadRequestError, TaskNotFoundError, SchedulerError) as error:
        messages.error(request, error.message)
