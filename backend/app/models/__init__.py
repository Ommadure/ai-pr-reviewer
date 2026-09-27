"""Importing this package registers every model on Base.metadata (Alembic relies on it)."""

from app.models.installation import Installation
from app.models.job import Job
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import LLMCall, RepoConfigRecord, ReviewCommentRecord, ReviewRun
from app.models.user import User, UserInstallation
from app.models.webhook_delivery import WebhookDelivery

__all__ = [
    "Installation",
    "Job",
    "LLMCall",
    "PullRequest",
    "RepoConfigRecord",
    "Repository",
    "ReviewCommentRecord",
    "ReviewRun",
    "User",
    "UserInstallation",
    "WebhookDelivery",
]
