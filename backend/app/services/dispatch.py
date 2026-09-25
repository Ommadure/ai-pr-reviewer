"""How the API hands work to Celery. A seam so tests can record jobs instead."""

from typing import Protocol

from app.services.webhook_router import ReviewJob


class ReviewDispatcher(Protocol):
    def enqueue_review(self, job: ReviewJob) -> None: ...


class CeleryReviewDispatcher:
    def enqueue_review(self, job: ReviewJob) -> None:
        # Imported lazily so importing the API doesn't configure Celery.
        from app.workers.tasks import review_pull_request

        review_pull_request.delay(job.run_id)


def get_review_dispatcher() -> ReviewDispatcher:
    return CeleryReviewDispatcher()
