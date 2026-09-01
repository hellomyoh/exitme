"""JWT 인증 — access 15분(Bearer) + refresh 14일(httpOnly Secure 쿠키, 회전). ADR-003.

회원 단일 등급. 모든 소유 리소스는 user_id 격리 (ARCHITECTURE §6).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/auth")
bearer = HTTPBearer(auto_error=False)

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(hours=1)  # 세션 1시간 유지(사용자 지시) — refresh 회전 시마다 연장(롤링)
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
    email: str = Field(min_length=2, max_length=100)  # 로그인 ID — 이메일 형식 강제 해제 (관리자 발급 ID 허용)
    password: str = Field(min_length=6, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: Credentials, response: Response, session: Session = Depends(get_session)) -> TokenOut:
    if not get_settings().allow_open_registration:
        # 계정은 관리자가 발급 (2026-09-01) — 공개 가입 차단
        raise HTTPException(status_code=403, detail="공개 가입이 비활성화되어 있습니다 — 관리자에게 계정을 요청하세요")
    if session.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status_code=409, detail="email already registered")
    user = User(
        email=body.email,
        password_hash=bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode(),
    )
    session.add(user)
    session.commit()
    _set_refresh_cookie(response, user.id)
    return TokenOut(access_token=_make_token(user.id, "access", ACCESS_TTL),
                    must_change_password=user.must_change_password)


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, response: Response, session: Session = Depends(get_session)) -> TokenOut:
    user = session.scalar(select(User).where(User.email == body.email))
    if user is None or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="invalid credentials")
    _set_refresh_cookie(response, user.id)
    return TokenOut(access_token=_make_token(user.id, "access", ACCESS_TTL),
                    must_change_password=user.must_change_password)


@router.post("/refresh", response_model=TokenOut)
def refresh(response: Response, refresh_token: str | None = Cookie(default=None)) -> TokenOut:
    if refresh_token is None:
        raise HTTPException(status_code=401, detail="missing refresh token")
    user_id = _decode(refresh_token, "refresh")
    _set_refresh_cookie(response, user_id)  # 회전
    return TokenOut(access_token=_make_token(user_id, "access", ACCESS_TTL))


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/change-password")
def change_password(body: PasswordChange, user_id: int = Depends(current_user_id),
                    session: Session = Depends(get_session)) -> dict:
    """비밀번호 변경 — 현재 비밀번호 확인 후 교체 (2026-08-31 설정 메뉴)."""
    user = session.get(User, user_id)
    if user is None or not bcrypt.checkpw(body.current_password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=403, detail="현재 비밀번호가 일치하지 않습니다")
    user.password_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt()).decode()
    user.must_change_password = False  # 발급 계정 첫 변경 완료 (2026-09-01)
    session.commit()
    return {"changed": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    """로그아웃 — refresh 쿠키 삭제 (2026-08-31 지시). access 는 클라이언트 메모리에서 폐기."""
    response.delete_cookie("refresh_token", path="/", secure=True, httponly=True, samesite="strict")
    return {"logged_out": True}


class MeOut(BaseModel):
    id: int
    login: str
    is_admin: bool
    must_change_password: bool


@router.get("/me")
def me(user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> MeOut:
    u = session.get(User, user_id)
    return MeOut(id=u.id, login=u.email, is_admin=u.is_admin,
                 must_change_password=u.must_change_password)


def require_admin(user_id: int = Depends(current_user_id),
                  session: Session = Depends(get_session)) -> User:
    u = session.get(User, user_id)
    if u is None or not u.is_admin:
        raise HTTPException(status_code=403, detail="관리자 전용 기능입니다")
    return u


class AdminUserIn(BaseModel):
    login: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6, max_length=128)


@router.get("/admin/users")
def admin_list_users(_admin: User = Depends(require_admin),
                     session: Session = Depends(get_session)) -> dict:
    rows = session.scalars(select(User).order_by(User.id)).all()
    return {"items": [
        {"id": u.id, "login": u.email, "is_admin": u.is_admin,
         "must_change_password": u.must_change_password,
         "created_at": u.created_at.isoformat() if u.created_at else None}
        for u in rows
    ]}


@router.post("/admin/users", status_code=201)
def admin_create_user(body: AdminUserIn, _admin: User = Depends(require_admin),
                      session: Session = Depends(get_session)) -> dict:
    """계정 발급 (2026-09-01 지시) — 첫 로그인에서 비밀번호 변경이 강제된다."""
    if session.scalar(select(User).where(User.email == body.login)):
        raise HTTPException(status_code=409, detail="이미 존재하는 아이디입니다")
    u = User(email=body.login,
             password_hash=bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode(),
             must_change_password=True)
    session.add(u)
    session.commit()
    return {"id": u.id, "login": u.email, "must_change_password": True}


def ensure_admin_account(session: Session) -> None:
    """기본 관리자 부트스트랩 — myoh 가 없으면 생성 (2026-09-01 지시, 멱등)."""
    if session.scalar(select(User).where(User.email == "myoh")) is None:
        session.add(User(email="myoh",
                         password_hash=bcrypt.hashpw(b"ansdud!", bcrypt.gensalt()).decode(),
                         is_admin=True))
        session.commit()
