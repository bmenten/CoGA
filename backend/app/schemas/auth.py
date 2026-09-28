"""Users, sign-in and sign-up."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .common import (
    ApiId,
)


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    affiliation: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    id: ApiId
    username: str
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    affiliation: Optional[str] = None
    is_active: bool
    role: str
    projects: List[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(arbitrary_types_allowed=True)


class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    projects: Optional[List[str]] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class SignupAck(BaseModel):
    """Generic signup acknowledgement.

    Returned for every signup request regardless of whether the submitted email
    is already registered, so the endpoint cannot be used to enumerate accounts.
    """

    detail: str


class UserRefOut(BaseModel):
    """Minimal user reference used for assignment/reviewer pickers."""

    id: ApiId
    username: str
    email: EmailStr
    first_name: str = ""
    last_name: str = ""

    model_config = ConfigDict(arbitrary_types_allowed=True)
