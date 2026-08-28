"""차트 레이아웃·드로잉 저장 API — 소유자 격리 (feature-chart §8·§10)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id
from app.db import get_session
from app.models import ChartDrawing, ChartLayout, Instrument

router = APIRouter(prefix="/chart")

MAX_JSON_BYTES = 1_000_000  # 드로잉 JSON 상한 1MB (feature-chart §10)


class LayoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: dict


@router.get("/layouts")
def list_layouts(user_id: int = Depends(current_user_id), session: Session = Depends(get_session)) -> dict:
    rows = session.scalars(select(ChartLayout).where(ChartLayout.user_id == user_id)).all()
    return {"items": [{"name": r.name, "config": r.config} for r in rows]}


@router.put("/layouts")
def save_layout(
    body: LayoutIn,
    user_id: int = Depends(current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    row = session.scalar(
        select(ChartLayout).where(ChartLayout.user_id == user_id, ChartLayout.name == body.name)
    )
    if row is None:
        row = ChartLayout(user_id=user_id, name=body.name, config=body.config)
        session.add(row)
    else:
        row.config = body.config
    session.commit()
    return {"saved": body.name}


class DrawingIn(BaseModel):
    items: dict


@router.get("/drawings")
def get_drawings(
    code: str,
    user_id: int = Depends(current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    if inst is None:
        raise HTTPException(status_code=404, detail="unknown code")
    row = session.scalar(
        select(ChartDrawing).where(ChartDrawing.user_id == user_id, ChartDrawing.instrument_id == inst.id)
    )
    return {"code": code, "items": row.items if row else {}}


@router.put("/drawings")
def save_drawings(
    code: str,
    body: DrawingIn,
    request: Request,
    user_id: int = Depends(current_user_id),
    session: Session = Depends(get_session),
) -> dict:
    if int(request.headers.get("content-length", 0)) > MAX_JSON_BYTES:
        raise HTTPException(status_code=413, detail="drawing payload too large")
    inst = session.scalar(select(Instrument).where(Instrument.code == code))
    if inst is None:
        raise HTTPException(status_code=404, detail="unknown code")
    row = session.scalar(
        select(ChartDrawing).where(ChartDrawing.user_id == user_id, ChartDrawing.instrument_id == inst.id)
    )
    if row is None:
        row = ChartDrawing(user_id=user_id, instrument_id=inst.id, items=body.items)
        session.add(row)
    else:
        row.items = body.items
    session.commit()
    return {"saved": code}
