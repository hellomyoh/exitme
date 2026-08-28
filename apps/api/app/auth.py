"""JWT 인증 — access 15분(Bearer) + refresh 14일(httpOnly Secure 쿠키, 회전). ADR-003.

회원 단일 등급. 모든 소유 리소스는 user_id 격리 (ARCHITECTURE §6).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/auth")
bearer = HTTPBearer(auto_error=False)

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=14)
ALGO = "HS256"


def _make_token(user_id: int, kind: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "kind": kind, "iat": now, "exp": now + ttl},
        get_settings().jwt_secret,
        algorithm=ALGO,
    )


def _decode(token: str, kind: str) -> int:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")
    if payload.get("kind") != kind:
        raise HTTPException(status_code=401, detail="wrong token kind")
    return int(payload["sub"])


def _set_refresh_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        "refresh_token",
        _make_token(user_id, "refresh", REFRESH_TTL),
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=int(REFRESH_TTL.total_seconds()),
        path="/",
    )


def current_user_id(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> int:
    if cred is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return _decode(cred.credentials, "access")


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: Credentials, response: Response, session: Session = Depends(get_session)) -> TokenOut:
    if session.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status_code=409, detail="email already registered")
    user = User(
        email=body.email,
        password_hash=bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode(),
    )
    session.add(user)
    session.commit()
    _set_refresh_cookie(response, user.id)
    return TokenOut(access_token=_make_token(user.id, "access", ACCESS_TTL))


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, response: Response, session: Session = Depends(get_session)) -> TokenOut:
    user = session.scalar(select(User).where(User.email == body.email))
    if user is None or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="invalid credentials")
    _set_refresh_cookie(response, user.id)
    return TokenOut(access_token=_make_token(user.id, "access", ACCESS_TTL))


@router.post("/refresh", response_model=TokenOut)
def refresh(response: Response, refresh_token: str | None = Cookie(default=None)) -> TokenOut:
    if refresh_token is None:
        raise HTTPException(status_code=401, detail="missing refresh token")
    user_id = _decode(refresh_token, "refresh")
    _set_refresh_cookie(response, user_id)  # 회전
    return TokenOut(access_token=_make_token(user_id, "access", ACCESS_TTL))
