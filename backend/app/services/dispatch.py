"""How the API hands work to Celery. A seam so tests can record jobs instead."""

from typing import Protocol

from app.services.commands import CommandJob
from app.services.webhook_router import ReviewJob


class ReviewDispatcher(Protocol):
    def enqueue_review(self, job: ReviewJob) -> None: ...
    def enqueue_command(self, job: CommandJob) -> None: ...


class CeleryReviewDispatcher:
    # Tasks are imported lazily so importing the API doesn't configure Celery.
    def enqueue_review(self, job: ReviewJob) -> None:
        from app.workers.tasks import review_pull_request

        review_pull_request.delay(job.run_id)

    def enqueue_command(self, job: CommandJob) -> None:
        from app.workers.tasks import handle_pr_command

        handle_pr_command.delay(job.to_dict())


def get_review_dispatcher() -> ReviewDispatcher:
    return CeleryReviewDispatcher()
