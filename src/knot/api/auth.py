"""User-management router — admin only.

Endpoints under ``/auth``:
  - GET    /auth/me                    current principal (any authenticated user)
  - GET    /auth/users                 list users (admin)
  - POST   /auth/users                 create user; returns raw api_key once (admin)
  - POST   /auth/users/{username}/rotate    rotate user's api_key (admin)
  - DELETE /auth/users/{username}      delete user (admin)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from knot import db
from knot.security import Principal, require_admin, require_user
from knot.db import users


_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$"


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateUserBody(_StrictBase):
    username: str = Field(pattern=_NAME_PATTERN)
    is_admin: bool = False
    kind: users.PrincipalKind = users.PrincipalKind.USER
    email: str | None = None
    display_name: str | None = None


class CreatedUser(_StrictBase):
    username: str
    is_admin: bool
    kind: users.PrincipalKind
    api_key: str  # shown only once; client stores


class UserRow(_StrictBase):
    username: str
    is_admin: bool
    kind: str
    email: str | None
    display_name: str | None
    created_by: str | None
    created_at: str


class WhoAmI(_StrictBase):
    username: str
    is_admin: bool


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=WhoAmI)
def me(principal: Principal = Depends(require_user)) -> WhoAmI:
    return WhoAmI(username=principal.username, is_admin=principal.is_admin)


@router.get(
    "/users",
    response_model=list[UserRow],
    dependencies=[Depends(require_admin)],
)
def list_users() -> list[UserRow]:
    with db.connect() as conn:
        return [UserRow(**r) for r in users.list_users(conn)]


@router.post(
    "/users",
    response_model=CreatedUser,
    status_code=201,
)
def create_user(
    body: CreateUserBody,
    creator: Principal = Depends(require_admin),
) -> CreatedUser:
    with db.connect() as conn:
        if users.get_user(conn, body.username) is not None:
            raise HTTPException(409, f"User {body.username!r} already exists.")
        user, raw = users.create_user(
            conn,
            username=body.username,
            is_admin=body.is_admin,
            kind=body.kind,
            email=body.email,
            display_name=body.display_name,
            created_by=creator.username,
        )
    return CreatedUser(
        username=user.username,
        is_admin=user.is_admin,
        kind=user.kind,
        api_key=raw,
    )


@router.post(
    "/users/{username}/rotate",
    response_model=CreatedUser,
    dependencies=[Depends(require_admin)],
)
def rotate_key(username: str) -> CreatedUser:
    with db.connect() as conn:
        user = users.get_user(conn, username)
        if user is None:
            raise HTTPException(404, f"User {username!r} not found.")
        raw = users.rotate_key(conn, username=username)
    return CreatedUser(
        username=user.username,
        is_admin=user.is_admin,
        kind=user.kind,
        api_key=raw,
    )


@router.delete(
    "/users/{username}",
    dependencies=[Depends(require_admin)],
)
def delete_user(username: str) -> dict[str, Any]:
    with db.connect() as conn:
        deleted = users.delete_user(conn, username=username)
    if not deleted:
        raise HTTPException(404, f"User {username!r} not found.")
    return {"deleted": username}
