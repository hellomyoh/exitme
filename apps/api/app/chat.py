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

# ── 3층 프롬프트 (2026-09-04 지시): 본문(교체 가능·관리자) + 코어 계약(고정) + 사용자 추가 지침
# 본문은 app_settings.chat_system_prompt 로 전체 교체 가능 — 비어 있으면 아래 기본 사용.
DEFAULT_BODY = """당신은 ExitMe 의 매매 도우미입니다. ExitMe 는 코스피200 ETF·나스닥 ETF 를
규칙 기반 전략으로 운용하는 개인용 웹 시스템이고, 당신은 이 시스템 안에서 사용자의 계좌 데이터를
도구로 직접 조회해 설명하는 어시스턴트입니다. 항상 한국어로 답합니다.

## 역할
- 사용자의 실전매매 계좌(자산·보유·일지)와 주문표·시뮬레이션 결과를 조회해 설명한다.
- 전략 규칙(왜 이 주문이 나왔는지, 왜 팔라는 건지)을 근거와 함께 풀어 설명한다.

## 전략 지식 (정본 요약)
- KR — RAVG v2.5 (TIGER/KODEX 200 + KODEX 레버리지):
  · 노출 E = min(레짐별 Emax, ½·목표σ/σd + ½·σref/σd), 목표 하방변동성 0.20.
  · 레짐 = MA200 기반 3단: 상승/중립/하락, Emax 1.30/0.65/0.20, 이탈 완충 ε 2%.
  · 그리드 Grid = 0.75×ATR20/종가 (0.8~4% 클립) — 종가 −G/−2G/−3G 지정가 매수(예산 50/30/20),
    로트별 매수가+G 익절(지정가). 상승장에서는 익절 없이 코어 보유.
  · 시가가 전일종가 −1.5×ATR 이하 출발 시 그리드 전량 취소. σ20 ≥ 35% 면 레버리지 전량 청산.
  · 리밸런싱 밴드 ±5%p — 목표와의 괴리가 이 안이면 재조정하지 않음.
- US — TF (QQQ): MA200 위에서 보유, 종가가 MA200 −2% 이탈 시 다음날 시가 매도. 그리드 없음.
- 주문표는 "전일 종가 시점 상태"의 함수(B안) — 당일 체결 등록은 다음 주문표부터 반영.
  실행일이 지난 계획 스냅샷은 불변(그날 아침의 계획 보존).
- 발주는 사용자가 본인 HTS 에서 직접 하고 결과만 등록한다. 부분 이행도 허용되며 원장은 정합하다.
- 수동 등록 보유분은 단일 로트라 익절이 전량으로 나온다(설계 정합 — 10년 측정상 모델도 익절일 59% 전량 매도).

## 답변 스타일
- **핵심만 간결하게**: 결론부터 한두 문장으로 답하고, 필요한 근거만 짧게 덧붙인다. 서론·복명복창·
  불필요한 배경 설명 금지. 짧은 질문에는 짧게 답한다.
- 여러 항목 비교·나열은 마크다운 표로. 금액은 천 단위 구분(예: 32,093,398원).
- 사용자가 "자세히"를 요구할 때만 길게 설명한다.
- 매수/매도 판단을 묻는 질문에는 전략 규칙이 말하는 바를 설명하되, 모의·과거 데이터 기반이며
  투자 권유가 아님을 짧게 덧붙인다.
"""

# 코어 계약 — 관리자가 본문을 교체해도 항상 첨부 (도구 하네스 무결성·수치 근거·단위)
CORE_CONTRACT = """## 시스템 계약 (항상 적용 — 위 내용과 충돌하면 이 절이 우선)
- 계좌·주문·수치 질문은 반드시 도구로 조회한 뒤 답한다. 조회 없이 수치를 추정하거나 지어내지 않는다.
- 포트가 여러 개인데 어떤 포트인지 불명확하면 list_portfolios 로 확인 후, 문맥상 명백하지 않으면 되묻는다.
- 도구가 error 를 돌려주면 그 사실을 숨기지 말고 무엇이 실패했는지 말한다.
- 미국 포트의 금액·가격은 센트 정수로 저장 — 표시할 때 100으로 나눠 $ 로 표기한다. 한국은 원 그대로.
- 도구는 전부 읽기 전용 — 주문 실행·체결 등록·설정 변경은 할 수 없다. 요청받으면 화면 위치를 안내한다:
  체결 등록 = 실전매매, 알고리즘 변수 = 알고리즘 설정, 시뮬레이션 실행 = 시뮬레이터.
"""

CHAT_SYSTEM_KEY = "chat_system_prompt"


def _system_body(session) -> str:
    """관리자 전역 오버라이드 or 내장 기본 — 챗봇 본문 (설정 화면과 공유)."""
    from app.models import AppSetting
    row = session.get(AppSetting, CHAT_SYSTEM_KEY)
    return (row.value if row and row.value.strip() else DEFAULT_BODY)


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
          {"portfolio_id": {"type": "integer", "description": "생략 시 모델 포트폴리오 신호"},
           "market": {"type": "string", "enum": ["KR", "US"], "description": "포트 미지정 시 모델 신호의 시장 (기본 KR)"}}),
    _tool("list_backtests", "최근 백테스트(시뮬레이션) 목록과 KPI(총수익률·MDD·샤프 등).",
          {"limit": {"type": "integer", "description": "기본 10"}}),
    _tool("algorithm_params", "현재 알고리즘 변수 설정값(레지스트리) — 이름·현재값·기본값·범위·설명.", {}),
    _tool("trading_journal", "수동 주식 매매일지 조회 — 인자 없으면 전체 요약(일지별 보유 종목·수량·원가, 누적 실현손익 추이), journal_id 지정 시 그 일지의 상세 기록(종목별 FIFO 실현손익·수익률·보유기간·비용).",
          {"journal_id": {"type": "integer", "description": "생략 시 전체 요약"}}),
    _tool("price_history", "종목 일봉 시세(원주가) — 마지막 행이 최신 확정 종가. 장중 실시간 시세는 제공하지 않음(장 마감 후 배치로 당일 종가 적재). code 예: 102110(TIGER 200), 069500(KODEX 200), 122630(레버), QQQ.",
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
                if args.get("market") == "US":
                    from app.signals import _live_us_model
                    return _live_us_model(session, user_id)
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
            if name == "trading_journal":
                from app.mjournal import get_journal, journals_overview
                jid = args.get("journal_id")
                if jid:
                    return get_journal(int(jid), user_id=user_id, session=session)
                return journals_overview(user_id=user_id, session=session)
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
                # 원주가(raw) 그대로 — 실주문·주문표와 같은 기준 (수정주가는 차트 전용)
                return {"code": inst.code, "name": inst.name, "market": inst.market,
                        "note": "일봉 종가 기준 — 마지막 행이 최신 확정 종가(장중 실시간 아님)",
                        "items": [{"date": r.trade_date.isoformat(), "open": r.open_raw, "high": r.high_raw,
                                   "low": r.low_raw, "close": r.close_raw, "volume": r.volume} for r in rows]}
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
              "tools": tools, "tool_choice": "auto", "max_tokens": 4000},
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
           "price_history": "시세 조회", "trading_journal": "매매일지"}


@router.post("/chat")
def chat(body: ChatIn, user_id: int = Depends(current_user_id)) -> StreamingResponse:
    if not get_settings().openrouter_api_key:
        raise HTTPException(status_code=503,
                            detail="OPENROUTER_API_KEY 가 설정되지 않았습니다 — .env 에 키를 넣고 API 를 재시작하세요.")

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    # 프롬프트 조립: 본문(전역 오버라이드 or 기본) + 코어 계약(고정) + 사용자 추가 지침 (2026-09-04)
    with SessionLocal() as _s:
        from sqlalchemy import select
        from app.models import UserSettings
        body_text = _system_body(_s)
        _row = _s.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
        user_prompt = (_row.chat_prompt if _row else "") or ""

    def stream():
        sys_text = body_text + "\n\n" + CORE_CONTRACT + f"\n오늘: {date.today().isoformat()}"
        if user_prompt:
            sys_text += ("\n\n## 사용자 추가 지침 (시스템 계약과 충돌하면 계약이 우선)\n" + user_prompt)
        msgs: list[dict] = [{"role": "system", "content": sys_text}]
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
