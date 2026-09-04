"""사용자 설정 API — 알고리즘 파라미터 오버라이드 (2026-08-31 지시).

PARAM_REGISTRY 가 단일 정의: 라벨·도움말·범위·수정 가능 여부. 프런트는 이 목록으로 렌더링한다.
저장은 기본값과 다른 값만. 적용 범위: 시뮬레이터 잡(생성 시점 스냅샷)·포트 기준 주문표·미국 라이브 신호.
공용 KR 모델 신호 배치는 항상 기본값 (사용자별 신호가 아니므로).
"""
from __future__ import annotations

from dataclasses import fields as dc_fields

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user_id, require_admin
from app.db import get_session
from app.models import UserSettings
from app.strategy.params import Params

router = APIRouter()

# (key, 라벨, 도움말, min, max, 수정 가능, 그룹)
PARAM_REGISTRY: list[tuple[str, str, str, float, float, bool, str]] = [
    ("target_downside_vol", "목표 하방변동성", "E 공식의 목표치 — 하락 방향 변동성을 이 수준(연 13%)에 맞추도록 투입 비중을 조절합니다. 올리면 항상 더 공격적", 0.05, 0.40, True, "노출"),
    ("sigma_down_floor", "하방변동성 하한", "σ_down 이 이 값보다 작아도 이 값으로 간주 — 저변동 구간에서 노출 폭주 방지", 0.01, 0.10, True, "노출"),
    ("emax_bull", "상승장 노출 한도", "상승장 실효노출 상한 — 1.0 초과분은 레버리지로 채움", 0.5, 2.0, True, "노출"),
    ("emax_neutral", "중립장 노출 한도", "중립장 실효노출 상한", 0.1, 1.0, True, "노출"),
    ("emax_bear", "하락장 노출 한도", "하락장 실효노출 상한 — 방어의 최후선", 0.0, 0.5, True, "노출"),
    ("regime_buffer", "레짐 이탈 완충", "MA20/MA60 이탈 다리의 히스테리시스 — 경계 잔진동(휩쏘) 무시 폭", 0.0, 0.10, True, "레짐"),
    ("ma200_exit_buffer", "MA200 이탈 완충", "상승/하락 레짐 이탈의 MA200 다리 히스테리시스 — 종가가 MA200 을 이 폭만큼 관통해야 이탈. 마이크로 전환(휩쏘) 방지", 0.0, 0.05, True, "레짐"),
    ("grid_coef", "그리드 계수", "매수 간격 = 계수 × ATR ÷ 종가 — 클수록 더 깊은 하락에서만 매수", 0.25, 2.0, True, "그리드"),
    ("grid_min", "그리드 최소 간격", "변동성이 작아도 이보다 좁게 깔지 않음", 0.002, 0.02, True, "그리드"),
    ("grid_max", "그리드 최대 간격", "변동성이 커도 이보다 넓게 깔지 않음", 0.02, 0.10, True, "그리드"),
    ("grid_steps", "그리드 단계 수", "지정가 매수를 몇 단계로 나눌지", 1, 5, True, "그리드"),
    ("cash_buffer", "현금 버퍼", "매수 가용 현금에서 예약해 두는 비율 — 목표 비중은 깎지 않음", 0.0, 0.05, True, "그리드"),
    ("band", "리밸런싱 밴드", "목표와의 괴리가 이 폭(±)을 넘을 때만 축소·재조정 — 잔거래 방지. 레짐 전환일·하락장은 무시", 0.01, 0.15, True, "그리드"),
    ("gap_atr_mult", "갭 취소 배수", "시가가 전일종가 − 배수×ATR 이하로 출발하면 그리드 전량 취소", 0.5, 3.0, True, "그리드"),
    ("lev_strategic_ratio", "레버리지 전략 비중", "레버리지 예산 중 상시 보유(전략 트랙) 비율 — 나머지는 눌림목 전술 트랙", 0.0, 1.0, True, "레버리지"),
    ("lev_tact1_mult", "전술 1차 진입 배수", "레버리지 종가 < EMA20 − 배수×ATR 이면 1차 진입", 0.25, 2.0, True, "레버리지"),
    ("lev_tact2_mult", "전술 2차 진입 배수", "더 깊은 눌림에서 2차 진입", 0.5, 3.0, True, "레버리지"),
    ("sigma20_liquidate", "레버리지 강제청산 σ", "20일 연환산 변동성이 이 값을 넘으면 레버리지 전량 청산", 0.10, 0.60, True, "레버리지"),
    ("min_history", "워밍업 거래일", "지표 계산에 필요한 최소 이력 — 줄이면 초기 지표가 불안정", 100, 400, True, "기타"),
    # ── 시장 종속·구조 상수 — 수정 불가 (마켓별 자동 적용)
    ("tick", "호가 단위", "한국 5원 / 미국 1센트 — 시장이 정하는 값이라 수정 불가", 0, 0, False, "시장(자동)"),
    ("lev_multiple", "레버리지 배율", "2배(KODEX 레버리지·QLD) / 3배(TQQQ) — 종목이 정하는 값이라 수정 불가", 0, 0, False, "시장(자동)"),
    ("commission", "수수료율", "시장·증권사별 기본값 자동 적용 (시뮬레이터에서 케이스별 변경 가능)", 0, 0, False, "시장(자동)"),
    ("slippage_market", "슬리피지", "시장가성 청산에만 적용", 0, 0, False, "시장(자동)"),
    ("lev_tax", "레버리지 과세", "국내 보유기간과세 15.4% 단순화 / 미국 0(세전)", 0, 0, False, "시장(자동)"),
]
_DEFAULTS = {f.name: getattr(Params(), f.name) for f in dc_fields(Params) if f.name != "flags"}
_EDITABLE = {k for k, *_rest in PARAM_REGISTRY if _rest[4]}
_INT_KEYS = {"grid_steps", "min_history", "tick"}


def _row(session: Session, user_id: int) -> UserSettings:
    row = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if row is None:
        row = UserSettings(user_id=user_id, algo_params={})
        session.add(row)
        session.flush()
    return row


@router.get("/settings/algorithm")
def get_algo_settings(user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    row = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    overrides = dict(row.algo_params) if row else {}
    items = []
    for key, label, help_, lo, hi, editable, group in PARAM_REGISTRY:
        items.append({
            "key": key, "label": label, "help": help_, "group": group, "editable": editable,
            "default": _DEFAULTS[key], "value": overrides.get(key, _DEFAULTS[key]),
            "min": lo, "max": hi, "overridden": key in overrides,
        })
    return {"items": items, "note": "설정은 시뮬레이터(신규 실행)·내 포트 주문표·미국 신호에 적용됩니다. 공용 KR 모델 신호는 기본값으로 계산됩니다."}


class AlgoIn(BaseModel):
    values: dict[str, float]


@router.put("/settings/algorithm")
def put_algo_settings(body: AlgoIn, user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    bounds = {k: (lo, hi) for k, _l, _h, lo, hi, ed, _g in PARAM_REGISTRY if ed}
    overrides: dict = {}
    for k, v in body.values.items():
        if k not in _EDITABLE:
            raise HTTPException(status_code=422, detail=f"수정 불가 항목: {k}")
        lo, hi = bounds[k]
        if not (lo <= v <= hi):
            raise HTTPException(status_code=422, detail=f"{k}: {lo}~{hi} 범위여야 합니다 (입력 {v})")
        v = int(v) if k in _INT_KEYS else float(v)
        if v != _DEFAULTS[k]:
            overrides[k] = v
    row = _row(session, user_id)
    row.algo_params = overrides
    session.commit()
    return {"saved": len(overrides), "overridden_keys": sorted(overrides)}


# ── 챗봇 시스템 프롬프트 (전역·관리자 전용, 2026-09-04 지시) — 본문 전체 교체. 코어 계약은 코드 고정.
class ChatSystemIn(BaseModel):
    prompt: str = Field(max_length=8000)


@router.get("/settings/chat-system")
def get_chat_system(admin=Depends(require_admin),
                    session: Session = Depends(get_session)) -> dict:
    from app.chat import CHAT_SYSTEM_KEY, CORE_CONTRACT, DEFAULT_BODY
    from app.models import AppSetting
    row = session.get(AppSetting, CHAT_SYSTEM_KEY)
    return {"prompt": (row.value if row else "") or "",  # "" = 기본 사용 중
            "default": DEFAULT_BODY,                      # '기본값 불러오기'용
            "core_contract": CORE_CONTRACT}               # 항상 첨부되는 고정 계약 (표시용)


@router.put("/settings/chat-system")
def put_chat_system(body: ChatSystemIn, admin=Depends(require_admin),
                    session: Session = Depends(get_session)) -> dict:
    """빈 값 저장 = 초기화(내장 기본으로 복귀). 교체 중에는 이후 전략 개정이 자동 반영되지 않는다."""
    from app.chat import CHAT_SYSTEM_KEY
    from app.models import AppSetting
    row = session.get(AppSetting, CHAT_SYSTEM_KEY)
    text = body.prompt.strip()
    if not text:
        if row is not None:
            session.delete(row)
        session.commit()
        return {"saved": True, "using_default": True}
    if row is None:
        session.add(AppSetting(key=CHAT_SYSTEM_KEY, value=text))
    else:
        row.value = text
    session.commit()
    return {"saved": True, "using_default": False, "length": len(text)}


# ── 챗봇 추가 지침 (2026-09-04 지시) — 내장 시스템 프롬프트 뒤에 덧붙음. 안전 규칙은 대체 불가.
class ChatPromptIn(BaseModel):
    prompt: str = Field(max_length=4000)


@router.get("/settings/chat")
def get_chat_settings(user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    row = session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    return {"prompt": (row.chat_prompt if row else "") or ""}


@router.put("/settings/chat")
def put_chat_settings(body: ChatPromptIn, user_id: int = Depends(current_user_id),
                      session: Session = Depends(get_session)) -> dict:
    row = _row(session, user_id)
    row.chat_prompt = body.prompt.strip()
    session.commit()
    return {"saved": True, "length": len(row.chat_prompt)}


@router.post("/settings/algorithm/reset")
def reset_algo_settings(user_id: int = Depends(current_user_id),
                        session: Session = Depends(get_session)) -> dict:
    row = _row(session, user_id)
    row.algo_params = {}
    session.commit()
    return {"reset": True}
