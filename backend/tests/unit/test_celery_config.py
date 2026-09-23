from app.workers.celery_app import celery_app
from app.workers.tasks import ping


def test_reliability_settings() -> None:
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_soft_time_limit < conf.task_time_limit


def test_review_tasks_route_to_reviews_queue() -> None:
    route = celery_app.amqp.router.route({}, "app.workers.tasks.review_pull_request")
    assert route["queue"].name == "reviews"


def test_ping_runs_locally() -> None:
    # .apply() executes in-process, no broker needed.
    assert ping.apply().get() == "pong"
