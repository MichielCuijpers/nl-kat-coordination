import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .ranker import Ranker


class BoefjeRanker(Ranker):
    MAX_PRIORITY = 1000
    MAX_DAYS = 7

    # TODO: type Task
    def rank(self, task: Any) -> int:
        """When a task hasn't run in a while it needs to be run sooner. We want
        a task to get a priority of 3 when `max_days` days are gone by, and
        thus it should have a lower bound of 3 for every task that has run past
        those`max_days`.

        3 has been chosen as a lower bound because new tasks that have not yet
        run before will get the priority of 2. And tasks created by the user
        (from rocky) will get a priority of 1.

        Before the end of those `max_days` we want to prioritize a task within
        a range from 3 to the maximum value of `max_priority`.
        """
        max_priority = self.MAX_PRIORITY
        max_days_in_seconds = self.MAX_DAYS * (60 * 60 * 24)
        grace_period = timedelta(seconds=self.ctx.config.pq_populate_grace_period)

        prior_tasks = self.ctx.task_store.get_tasks_by_hash(task.hash)

        # New tasks that have not yet run before
        if prior_tasks is None or not prior_tasks:
            return 2

        # Make sure that we don't have tasks that are still in the grace period
        time_since_grace_period = ((datetime.now(timezone.utc) - prior_tasks[0].modified_at) - grace_period).seconds
        if time_since_grace_period < 0:
            return -1

        if time_since_grace_period >= max_days_in_seconds:
            return 3

        # Iterate over the prior tasks (limit to 10)
        for prior_task in prior_tasks:
            # How long did it take for the task to run?
            duration = (prior_task.finished_at - prior_task.started_at).seconds
            self.logger.info(duration)

            # How many objects where created by the task?
            children = self.ctx.services.octopoes.get_children_by_ooi(
                organisation_id=task.organisation_id,
                reference=prior_task.input_ooi.reference,
            )
            self.logger.info(children)

            # How many findings were generated by the task?
            findings = self.ctx.services.octopoes.get_findings_by_ooi(
                organisation_id=task.organisation_id,
                reference=prior_task.input_ooi.reference,
            )
            self.logger.info(findings)

        return int(3 + (max_priority - 3) * (1 - time_since_grace_period / max_days_in_seconds))


class BoefjeRankerTimeBased(Ranker):
    """A timed-based BoefjeRanker allows for a specific time to be set for the
    task to be ranked. You'll be able to rank jobs with a specific time
    element. Epoch time is used allows the score and used as the priority on
    the priority queue. This allows for time-based scheduling of jobs.
    """

    def rank(self, obj: Any) -> int:
        minimum = datetime.today() + timedelta(days=1)
        maximum = minimum + timedelta(days=7)
        return random.randint(int(minimum.timestamp()), int(maximum.timestamp()))
