"""매매 도우미 챗봇 — OpenRouter tool-calling 하네스 (2026-09-04 지시).

- LLM 프로바이더: OpenRouter (OpenAI 호환 /chat/completions). OPENROUTER_API_KEY 만 넣으면 동작.
- 도구는 전부 읽기 전용이며 로그인 사용자 소유 데이터로 스코프된다. 쓰기 도구 없음 (하네스 안전 원칙).
- 대화 이력은 서버에 저장하지 않는다(무상태) — 클라이언트가 messages 를 보관해 보낸다.
- 응답은 SSE: {"type":"tool","name":...} 진행 이벤트 → {"type":"final","content":...} 1회.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.config import get_settings
from app.db import SessionLocal

log = logging.getLogger(__name__)
router = APIRouter()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """당신은 ExitMe(주식 ETF 자동 전략 시스템)의 매매 도우미입니다. 한국어로 답합니다.

시스템 지식:
- KR 전략 RAVG v2.5: 코스피200 ETF(TIGER/KODEX 200)+KODEX 레버리지. 목표 하방변동성 0.20 기반
  노출 E = min(레짐별 Emax, ½T/σd+½σref/σd). 레짐은 MA200±2% 3단(상승/중립/하락, Emax 1.30/0.65/0.20).
  그리드: Grid = 0.75×ATR20/종가 (0.8~4% 클립), 종가 −G/−2G/−3G 지정가 매수(예산 50/30/20),
  로트별 +G 익절(상승장은 코어로 익절 없음). σ20≥35% 전량 청산. 리밸런싱 밴드 ±5%p.
- US 전략 TF: QQQ 를 MA200 위에서 보유, 2% 이탈 시 다음날 시가 매도.
- 주문표는 전일 종가 상태의 함수(B안)이고, 실행일이 지난 계획 스냅샷은 불변이다.
- 체결은 사용자가 HTS 에서 직접 하고 이 시스템에 등록한다. 부분 이행도 허용되며 원장은 상태 기반으로 정합.

규칙:
- 도구로 조회한 실제 데이터에 근거해 답하고, 조회 없이 계좌 수치를 지어내지 않는다.
- 금액은 원/달러 단위 구분(미국 포트 금액·가격은 센트 저장 — 표시할 때 100으로 나눠 $ 표기).
- 모의·과거 데이터 기반이며 투자 권유가 아님을 민감한 판단 질문에서 상기시킨다.
- 간결하게, 표가 유용하면 마크다운 표로.
"""


# ── 도구 정의 (OpenAI tools 스키마) ──────────────────────────────────────────
def _tool(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required or []}}}


TOOLS = [
    _tool("list_portfolios", "사용자의 실전매매 포트폴리오 목록(id·이름·시장)을 조회한다.", {}),
    _tool("portfolio_summary", "포트폴리오 자산 요약 — 총자산·현금·주식·실현/평가손익·TWR·보유 종목별 상세.",
          {"portfolio_id": {"type": "integer", "description": "생략 시 기본 포트"}}),
    _tool("portfolio_journal", "일자별 매매 일지 — 그날의 주문표(계획)와 실제 체결, 일간 수익률.",
          {"portfolio_id": {"type": "integer"}, "days": {"type": "integer", "description": "최근 N일 (기본 10)"}}),
    _tool("order_sheet", "다음 거래일 주문표 — 익절/그리드 지정가·수량과 계산 기준 상태. 포트 지정 시 그 계좌 기준.",
          {"portfolio_id": {"type": "integer", "description": "생략 시 모델 포트폴리오 신호"}}),
    _tool("list_backtests", "최근 백테스트(시뮬레이션) 목록과 KPI(총수익률·MDD·샤프 등).",
          {"limit": {"type": "integer", "description": "기본 10"}}),
    _tool("algorithm_params", "현재 알고리즘 변수 설정값(레지스트리) — 이름·현재값·기본값·범위·설명.", {}),
    _tool("price_history", "종목 최근 일봉 시세 (원시가). code 예: 102110(TIGER 200), 069500(KODEX 200), 122630(레버), QQQ.",
          {"code": {"type": "string"}, "days": {"type": "integer", "description": "기본 30"}}, ["code"]),
]


def _run_tool(name: str, args: dict, user_id: int) -> dict:
    """도구 실행 — 전부 읽기 전용, user_id 스코프. 실패는 {'error': ...} 로 모델에 전달."""
    with SessionLocal() as session:
        try:
            if name == "list_portfolios":
                from app.portfolios import list_portfolios
                return list_portfolios(user_id=user_id, session=session)
            if name == "portfolio_summary":
                from app.portfolios import portfolio_summary
                return portfolio_summary(portfolio_id=args.get("portfolio_id"),
                                         include_costs=True, user_id=user_id, session=session)
            if name == "portfolio_journal":
                from app.portfolios import portfolio_journal
                out = portfolio_journal(portfolio_id=args.get("portfolio_id"),
                                        days=int(args.get("days") or 10),
                                        user_id=user_id, session=session)
                out["items"] = out["items"][: int(args.get("days") or 10)]
                return out
            if name == "order_sheet":
                pid = args.get("portfolio_id")
                if pid:
                    # 실제 주문표 엔드포인트와 동일 디스패치: US 포트 → TF, KR 포트 → RAVG (signals 참조)
                    from app.models import TradePortfolio
                    from app.signals import _live_us_model, _portfolio_orders, _tf_portfolio_orders
                    pf_row = session.get(TradePortfolio, int(pid))
                    if pf_row is None or pf_row.user_id != user_id:
                        return {"error": "portfolio not found"}
                    if pf_row.market == "US":
                        base = _live_us_model(session, user_id)
                        if base.get("status") == "OK":
                            base.update(_tf_portfolio_orders(session, pf_row, int(pid)))
                        return base
                    return _portfolio_orders(session, int(pid), user_id)
                from app.signals import get_daily_signal
                return get_daily_signal(date_=None, market="KR", _user=user_id, session=session)
            if name == "list_backtests":
                from app.backtests import list_backtests
                out = list_backtests(cursor=None, limit=int(args.get("limit") or 10),
                                     user_id=user_id, session=session)
                return out
            if name == "algorithm_params":
                from app.settings import get_algo_settings
                return get_algo_settings(user_id=user_id, session=session)
            if name == "price_history":
                from sqlalchemy import select
                from app.models import Instrument, OhlcvDaily
                inst = session.scalar(select(Instrument).where(Instrument.code == str(args["code"])))
                if inst is None:
                    return {"error": f"unknown code {args['code']}"}
                since = date.today() - timedelta(days=int(args.get("days") or 30) * 2)
                rows = session.execute(
                    select(OhlcvDaily).where(OhlcvDaily.instrument_id == inst.id,
                                             OhlcvDaily.trade_date >= since)
                    .order_by(OhlcvDaily.trade_date)).scalars().all()
                rows = rows[-int(args.get("days") or 30):]
                return {"code": inst.code, "name": inst.name, "market": inst.market,
                        "items": [{"date": r.trade_date.isoformat(), "open": r.open, "high": r.high,
                                   "low": r.low, "close": r.close, "volume": r.volume} for r in rows]}
            return {"error": f"unknown tool {name}"}
        except HTTPException as e:  # 소유권·404 등 — 모델이 이해할 메시지로
            return {"error": str(e.detail)}
        except Exception as e:  # noqa: BLE001 — 도구 실패가 대화를 죽이면 안 됨
            log.warning("chat tool %s failed: %s", name, e)
            return {"error": f"{type(e).__name__}: {e}"}


def _openrouter_call(messages: list[dict], tools: list[dict]) -> dict:
    """OpenRouter 1회 호출 — 테스트에서 monkeypatch 되는 경계."""
    s = get_settings()
    resp = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {s.openrouter_api_key}",
                 "HTTP-Referer": "https://github.com/hellomyoh/exitme",
                 "X-Title": "ExitMe"},
        json={"model": s.openrouter_model, "messages": messages,
              "tools": tools, "tool_choice": "auto", "max_tokens": 2000},
        timeout=120.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")
    return resp.json()


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


TOOL_KO = {"list_portfolios": "포트폴리오 목록", "portfolio_summary": "자산 요약",
           "portfolio_journal": "매매 일지", "order_sheet": "주문표",
           "list_backtests": "시뮬레이션 목록", "algorithm_params": "알고리즘 설정",
           "price_history": "시세 조회"}


@router.post("/chat")
def chat(body: ChatIn, user_id: int = Depends(current_user_id)) -> StreamingResponse:
    if not get_settings().openrouter_api_key:
        raise HTTPException(status_code=503,
                            detail="OPENROUTER_API_KEY 가 설정되지 않았습니다 — .env 에 키를 넣고 API 를 재시작하세요.")

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def stream():
        msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT + f"\n오늘: {date.today().isoformat()}"}]
        msgs += [m.model_dump() for m in body.messages]
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                data = _openrouter_call(msgs, TOOLS)
                choice = data["choices"][0]
                message = choice["message"]
                calls = message.get("tool_calls") or []
                if not calls:
                    yield sse({"type": "final", "content": message.get("content") or ""})
                    return
                msgs.append(message)
                for c in calls:
                    fname = c["function"]["name"]
                    yield sse({"type": "tool", "name": fname, "label": TOOL_KO.get(fname, fname)})
                    try:
                        fargs = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        fargs = {}
                    result = _run_tool(fname, fargs, user_id)
                    msgs.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)[:20000]})
            yield sse({"type": "final",
                       "content": "도구 호출이 너무 깊어져 중단했습니다 — 질문을 더 구체적으로 나눠주세요."})
        except Exception as e:  # noqa: BLE001 — 오류를 SSE 로 전달 (연결이 이미 200 이므로)
            log.warning("chat failed: %s", e)
            yield sse({"type": "error", "content": f"응답 생성 실패: {e}"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
