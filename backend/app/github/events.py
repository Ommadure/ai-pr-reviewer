"""Typed webhook payloads: only the fields we use.

Unknown fields are ignored (Pydantic's default), so GitHub adding fields never
breaks us, and a missing field we *do* rely on fails loudly at the boundary.
Reference: https://docs.github.com/en/webhooks/webhook-events-and-payloads
"""

from pydantic import BaseModel


class Account(BaseModel):
    login: str
    type: str  # "User" | "Organization" | "Bot"


class InstallationRef(BaseModel):
    id: int


class Installation(BaseModel):
    id: int
    account: Account


class RepositoryRef(BaseModel):
    """Short repository form used in installation payloads (no default_branch)."""

    id: int
    full_name: str
    private: bool


class Repository(BaseModel):
    id: int
    full_name: str
    private: bool
    default_branch: str
    owner: Account


class GitRef(BaseModel):
    ref: str
    sha: str


class PullRequest(BaseModel):
    id: int
    number: int
    title: str
    state: str  # "open" | "closed"
    draft: bool = False
    merged: bool = False
    user: Account
    head: GitRef
    base: GitRef


class InstallationEvent(BaseModel):
    action: str
    installation: Installation
    repositories: list[RepositoryRef] = []
    sender: Account


class InstallationRepositoriesEvent(BaseModel):
    action: str
    installation: Installation
    repositories_added: list[RepositoryRef] = []
    repositories_removed: list[RepositoryRef] = []
    sender: Account


class PullRequestEvent(BaseModel):
    action: str
    number: int
    pull_request: PullRequest
    repository: Repository
    installation: InstallationRef
    sender: Account


class Comment(BaseModel):
    id: int
    body: str
    user: Account


class Issue(BaseModel):
    number: int
    # Present only when the "issue" is actually a pull request.
    pull_request: dict[str, object] | None = None


class IssueCommentEvent(BaseModel):
    action: str
    issue: Issue
    comment: Comment
    repository: Repository
    installation: InstallationRef
    sender: Account
