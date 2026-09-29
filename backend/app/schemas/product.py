"""Product metadata: the running build and the releases."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class VersionOut(BaseModel):
    """The running build: its semantic version and git commit (TF-18 §2).

    Shown in the app footer and on every report footer (TF-15 §1).
    """

    version: str
    git_sha: str


class GithubReleaseOut(BaseModel):
    version: str
    name: Optional[str] = None
    published_at: datetime
    summary: str
    url: str
    prerelease: bool = False


class GithubReleaseCatalogOut(BaseModel):
    repository: str
    repository_url: str
    releases_url: str
    issues_url: str
    repo_visibility: Literal["private", "public", "unknown"] = "unknown"
    sync_status: Literal["ok", "unavailable"] = "unavailable"
    sync_error: Optional[str] = None
    fetched_at: Optional[datetime] = None
    releases: List[GithubReleaseOut] = Field(default_factory=list)
