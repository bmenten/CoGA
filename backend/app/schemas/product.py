"""Product metadata: releases."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


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
