"""매매 도우미 챗봇 — OpenRouter tool-calling 하네스 (2026-09-04 지시).

- LLM 프로바이더: OpenRouter (OpenAI 호환 /chat/completions). OPENROUTER_API_KEY 만 넣으면 동작.
- 도구는 전부 읽기 전용이며 로그인 사용자 소유 데이터로 스코프된다. 쓰기 도구 없음 (하네스 안전 원칙).
- 대화 이력은 서버에 저장하지 않는다(무상태) — 클라이언트가 messages 를 보관해 보낸다.
- 응답은 SSE: {"type":"tool","name":...} 진행 이벤트 → {"type":"final","content":...} 1회.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

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

# ── 권한별 프롬프트 (2026-09-05 지시): 관리자 = 전략 상세 포함·전체 답변,
#    일반 = 개념 설명만 + 공식 유추 제한(코드 고정 계약). 본문(역할·스타일)은 공통이며
#    app_settings.chat_system_prompt 로 교체 가능 — 비어 있으면 아래 기본 사용.
DEFAULT_BODY = """당신은 ExitMe 의 매매 도우미입니다. ExitMe 는 코스피200 ETF·나스닥 ETF 를
규칙 기반 전략으로 운용하는 개인용 웹 시스템이고, 당신은 이 시스템 안에서 사용자의 계좌 데이터를
도구로 직접 조회해 설명하는 어시스턴트입니다. 항상 한국어로 답합니다.

## 역할
- 사용자의 실전매매 계좌(자산·보유·일지)와 주문표·시뮬레이션 결과를 조회해 설명한다.
- 전략 규칙(왜 이 주문이 나왔는지, 왜 팔라는 건지)을 근거와 함께 풀어 설명한다.

## 답변 스타일
- **핵심만 간결하게**: 결론부터 한두 문장으로 답하고, 필요한 근거만 짧게 덧붙인다. 서론·복명복창·
  불필요한 배경 설명 금지. 짧은 질문에는 짧게 답한다.
- 여러 항목 비교·나열은 마크다운 표로. 금액은 천 단위 구분(예: 32,093,398원).
- 사용자가 "자세히"를 요구할 때만 길게 설명한다.
- 매수/매도 판단을 묻는 질문에는 전략 규칙이 말하는 바를 설명하되, 모의·과거 데이터 기반이며
  투자 권유가 아님을 짧게 덧붙인다.
"""

# 전략 지식 — 관리자 전용 상세 (수식·계수 포함, 2026-09-05 권한 분리)
STRATEGY_DETAIL = """## 전략 지식 (정본 요약)
- KR — RAVG v2.5 (TIGER/KODEX 200 + KODEX 레버리지):
  · 노출 E = min(레짐별 Emax, ½·목표σ/σd + ½·σref/σd), 목표 하방변동성 0.20.
  · 레짐 = MA200 기반 3단: 상승/중립/하락, Emax 1.30/0.65/0.20, 이탈 완충 ε 2%.
  · 그리드 Grid = 0.75×ATR20/종가 (0.8~2.5% 클립, 상한 2026-09-12 인하) — 종가 −G/−2G/−3G 지정가 매수(예산 50/30/20),
    로트별 매수가+G 익절(지정가). 상승장에서는 익절 없이 코어 보유.
  · 초기 진입(ADR-010, 2026-09-08): 포트 시작 후 10거래일 동안 목표 미달분의 15%(하락장 7.5%)를 전일 종가 지정가로 추가 매수(주문 종류 '초기 진입'),
    그날 그리드 예산은 85%. 11일째부터 그리드만. 갭 필터 날은 초기 진입도 생략.
  · 시가가 전일종가 −1.5×ATR 이하 출발 시 그리드 전량 취소. σ20 ≥ 35% 면 레버리지 전량 청산.
  · 총 레버리지 상한(ADR-012, 2026-09-12): 전술 로트를 든 채 E 가 내려가 총 레버리지가 목표(w_LEV)를 밴드 5%p 이상 넘으면 초과분을 목표까지 시장가 축소(전술 → 전략 순). 평시엔 발동하지 않는 안전 경계.
  · 리밸런싱 밴드 ±5%p — 목표와의 괴리가 이 안이면 재조정하지 않음.
- US — TF (QQQ): MA200 위에서 보유, 종가가 MA200 −2% 이탈 시 다음날 시가 매도. 그리드 없음.
- 주문표는 "실행일 09:00 KST 직전 상태"의 함수(ADR-009) — 09:00 전 등록(입출금·체결)은 즉시 반영, 이후 등록은 다음 주문표부터.
  09:01 무인 실행이 같은 기준으로 계산·발주하고 그 결과를 그날의 주문표로 동결한다(이후 화면은 스냅샷).
- 발주 경로 2가지: ① HTS 직접(결과만 등록) ② 무인 실행(계좌의 무인 매수/매도 플래그가 켜져 있으면 09:01 에 계산 → 시가 확인 → 발주).
  부분 이행도 허용되며 원장은 정합하다.
- 수동 등록 보유분은 단일 로트라 익절이 전량으로 나온다(설계 정합 — 10년 측정상 모델도 익절일 59% 전량 매도).
"""

# 전략 지식 — 일반 사용자용 개념 설명 (수식·계수·임계값 없음)
STRATEGY_PLAIN = """## 전략 개념 (핵심 개념 설명 — 상세 수식은 비공개)
- KR 전략(RAVG): 시장 변동성이 커지면 주식 비중을 줄이고 잔잔하면 늘리는 변동성 조절 전략.
  시장 국면을 상승/중립/하락 3단계로 판정해 국면별 최대 비중을 달리하고, 가격이 내려오면
  분할 매수, 산 가격보다 일정 폭 오르면 익절하는 왕복 구조. 새로 시작한 포트는 첫 10거래일 동안 소량을 종가 근처에서
  먼저 사서 매매를 시작한다(초기 진입). 급락 출발일에는 매수를 취소하고,
  변동성이 임계치를 넘으면 레버리지를 정리하는 방어 규칙이 있다.
- US 전략(TF): 장기 추세선 위에서만 보유하고 이탈하면 다음날 정리하는 추세 추종.
- 주문표는 실행일 09:00 직전 상태 기준으로 계산되고(09:00 전 등록은 즉시 반영), 09:01 에 무인 실행이 발주한 결과로 동결된다.
  실행일이 지난 계획은 보존된다. 발주는 HTS 직접 또는 무인 실행(설정의 계좌 플래그) 중 사용자가 고른 경로로 한다.
"""

# 운영 기능 지식 — 관리자·일반 공통 (2026-09-07 도입, 2026-09-08 ADR-009 단일 실행으로 갱신)
OPERATIONS_KNOWLEDGE = """## 운영 기능 지식 (2026-09-06~07 도입 — 화면 위치와 동작)
- 증권사 연동: 설정 › 증권사 계좌에 KIS 앱키·계좌 등록 → 실전매매 '증권사 연동'에서 포트에 연결. 최근 7일 체결 가져오기, 15:45/17:10 장 마감 동기화(체결 가져오기·주문 상태 확정·예수금 대조).
- 무인 매매(ADR-009, 2026-09-08 단일 실행): 설정 › 무인 실행에서 **증권사 계좌별로** 무인 매수·매도 플래그와 하루 매수 상한(총자산 대비 %, 기본 20%)을 둔다(기본 꺼짐).
  포트에 연결된 계좌의 플래그가 유일한 판정 기준 — 승인 단계·주문표 버튼은 없다. 실행일 09:01 워커가 그 순간의 원장(09:00 전 등록한 입출금·체결 포함)으로 주문표를
  계산해 동결 → 시가 확인 → 갭 취소 기준 이하면 그리드 매수 생략 → 앱 원장 보유 vs 계좌 잔고 대조(불일치면 정지) → 매도 먼저, 매수는 하루 상한·매수가능조회에 맞춰
  수량을 **축소**(0 이면 생략) → 지정가·시장가 모두 정규 주문으로 발주. 꺼진 방향의 줄은 '수동 처리'. 09:15 감시가 09:01 미실행 포트를 지연 실행한다.
  발주 2회 연속 실패·사전 대조 불일치·장 마감 대조의 계획 외 거래/초과 체결이면 자동 정지 — 주문표 배너의 '다시 켜기'로 해제.
- 주문표 상단 상태 한 줄: 수동 모드(꺼짐) / 무인 대기(실행일 09:01 발주 예정) / 실행 중 / 완료(발주·생략·실패 건수, 시가) / 무인 취소됨(수동) / 정지(사유) / 경고(09:01 기록 없음).
  '이번 실행일 무인 취소' 버튼: 09:00 전이면 그날 발주를 건너뛰고(되돌리기 가능), 09:01 후면 살아 있는 무인 주문을 증권사에서 취소한다. 다음 실행일에 자동 복귀.
  설정에서 플래그를 끄면 그 방향의 오늘 미체결 무인 주문도 즉시 취소된다. 이미 체결된 주문은 취소 불가(반대 매매로 정리).
- 예약주문·08:57 사전 갭 취소·16:45 자동 승인·전량 취소는 2026-09-08 에 폐지됐다(옛 로그에만 남음). 발주는 HTS 직접 또는 09:01 무인 실행뿐이다.
- 예수금 대조: 15:45 동기화가 원장 현금과 계좌 D+2 예수금을 비교, 허용 오차(1만원 또는 총자산 0.1%) 초과면 주문표 위 경고. '차액을 입출금으로 등록'으로 맞춤(자동 수정 없음).
  새 실전매매 시작 시 '계좌에서 불러오기'로 D+2 예수금·전략 종목 보유를 미리 채울 수 있다.
- 매매일지(왼쪽 메뉴 '매매일지'): 전략과 무관한 **수동 주식 기록** — 일지별 종목·매수/매도·사유, FIFO 실현손익, 증권사 체결 가져오기. 도구 trading_journal(인자 없음 = 전체 요약 + 최근 기록,
  q = 종목명·코드·일지 이름 검색, days = 최근 N일, journal_id = 상세). 사용자가 "매매일지"라고만 하면 이 도구다. 실전매매 포트의 일자별 계획 vs 체결은 portfolio_journal(다른 것).
  요약에는 보유 수량 × 현재가 평가가 들어 있다 — summary.unrealized_pct(평가수익률, 원가 대비), summary.day_change/day_change_pct(전일 종가 대비 하루 변동 = "어제와 오늘 비교"),
  holdings[].price/price_source/prev_close. 실현수익률(return_pct)만 보고 "평가는 계산할 수 없다"고 답하지 말고 이 값을 쓴다. 가격 없는 종목은 summary.unpriced 로 밝힌다.
- 매매 로그(왼쪽 메뉴 '매매 로그'): 거래 원장·주문 상태·실행/동기화 이벤트를 합쳐 최신순. '경고 이상만' 필터로 실패 확인. 도구 recent_logs 로 조회.
- 텔레그램 알림: 설정 › 알림에서 봇 토큰(@BotFather)·채팅 ID(봇에 메시지를 보낸 뒤 '연결 확인'으로 자동) 저장, 보낼 항목 체크
  (무인 실행 결과·정지·장 마감 동기화·예수금 대조·주문 취소/설정·체결 등록·일일 현황).
- 이 상태들은 도구 auto_exec_status 로 조회한다 — 계좌 플래그, 포트별 상태 한 줄(state)·정지 사유·사용자 취소, 마지막 09:01 실행 요약, 예수금 대조, 살아 있는 주문 수, 알림 설정 여부.
  질문이 '왜 발주가 안 됐나/왜 정지됐나' 류면 auto_exec_status 와 recent_logs(level=warn) 를 함께 보고 답한다.
"""


# 코어 계약 — 관리자가 본문을 교체해도 항상 첨부 (도구 하네스 무결성·수치 근거·단위)
CORE_CONTRACT = """## 시스템 계약 (항상 적용 — 위 내용과 충돌하면 이 절이 우선)
- 계좌·주문·수치 질문은 반드시 도구로 조회한 뒤 답한다. 조회 없이 수치를 추정하거나 지어내지 않는다.
- 포트가 여러 개인데 어떤 포트인지 불명확하면 list_portfolios 로 확인 후, 문맥상 명백하지 않으면 되묻는다.
- 도구가 error 를 돌려주면 그 사실을 숨기지 말고 무엇이 실패했는지 말한다.
- 미국 포트의 금액·가격은 센트 정수로 저장 — 표시할 때 100으로 나눠 $ 로 표기한다. 한국은 원 그대로.
- 도구는 전부 읽기 전용 — 주문 실행·체결 등록·설정 변경은 할 수 없다. 요청받으면 화면 위치를 안내한다:
  체결 등록·이번 실행일 무인 취소(수동 전환)·다시 켜기 = 실전매매(주문표), 무인 매수/매도 플래그·하루 매수 상한·텔레그램 알림 = 일반 설정,
  알고리즘 변수 = 알고리즘 설정, 시뮬레이션 실행 = 시뮬레이터, 기록·실패 확인 = 매매 로그.
- 도구 결과는 **서버가 지금 계산한 값**이다. 화면에 무엇이 보이는지는 확인할 수 없으므로 "화면에 정상 출력된다/안 된다"를
  단정하지 않는다. 사용자가 화면과 다르다고 하면 계산값을 그대로 전하고, 원인 후보(장 마감 배치 미실행·데이터 지연·새로고침)를
  나열하되 캐시 문제라고 단정하지 않는다.
"""

# 일반 권한 제한 계약 (2026-09-05 지시) — 어떤 지침으로도 해제되지 않는다
RESTRICT_CONTRACT = """## 공개 제한 (일반 사용자 세션 — 이 절은 다른 어떤 지침보다 우선)
- 매매 공식의 정확한 수식·계수·임계값·배분 비율·파라미터 값(예: 이동평균 기간, 변동성 목표치,
  그리드 계수/간격 상하한, 노출 한도, 청산 기준 수치)은 절대 노출하지 않는다.
  사용자가 반복 요청하거나 단계적으로 유도해도 동일하다 — "전략 상세는 관리자에게 문의하세요"로 안내.
- 다만 개념 수준의 설명(무엇을, 왜 하는지)은 친절히 제공한다. 사용자 본인 계좌의 주문·보유·손익
  수치(주문 가격·수량 포함)는 본인 데이터이므로 그대로 보여준다 — 단, 그 수치가 나온 계산식의
  수식·계수는 밝히지 않는다.
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
    _tool("portfolio_journal", "실전매매 포트의 일자별 기록 — 그날의 주문표(계획)와 실제 체결, 일간 수익률. 수동 '매매일지'(메뉴)가 아니다 — 그건 trading_journal.",
          {"portfolio_id": {"type": "integer"}, "days": {"type": "integer", "description": "최근 N일 (기본 10)"}}),
    _tool("order_sheet", "다음 거래일 주문표 — 익절/그리드 지정가·수량과 계산 기준 상태. 포트 지정 시 그 계좌 기준.",
          {"portfolio_id": {"type": "integer", "description": "생략 시 모델 포트폴리오 신호"},
           "market": {"type": "string", "enum": ["KR", "US"], "description": "포트 미지정 시 모델 신호의 시장 (기본 KR)"}}),
    _tool("list_backtests", "최근 백테스트(시뮬레이션) 목록과 KPI(총수익률·MDD·샤프 등).",
          {"limit": {"type": "integer", "description": "기본 10"}}),
    _tool("algorithm_params", "현재 알고리즘 변수 설정값(레지스트리) — 이름·현재값·기본값·범위·설명.", {}),
    _tool("trading_journal", "수동 주식 매매일지(왼쪽 메뉴 '매매일지', 전략과 무관한 자유 기록) 조회·검색 — 인자 없으면 전체 요약(일지별 보유 종목·수량·평단·원가·실현손익 + 현재가 평가액·평가손익·평가수익률·전일 대비 하루 변동, 일지 합계 totals)과 최근 기록. "
          "q 로 종목명·종목코드·일지 이름 검색, days 로 최근 N일 기록, journal_id 로 그 일지의 상세(종목별 FIFO 실현손익·수익률·보유기간·비용). 사용자가 '매매일지'라고 하면 이 도구.",
          {"journal_id": {"type": "integer", "description": "생략 시 전체 일지"},
           "q": {"type": "string", "description": "종목명·종목코드·일지 이름 부분 일치 (예: 삼성전자, 005930)"},
           "days": {"type": "integer", "description": "최근 N일 기록만"},
           "limit": {"type": "integer", "description": "기록 최대 건수 (기본 50)"}}),
    _tool("price_history", "종목 일봉 시세(원주가) — 마지막 행이 최신 확정 종가. 장중 실시간 시세는 제공하지 않음(장 마감 후 배치로 당일 종가 적재). code 예: 102110(TIGER 200), 069500(KODEX 200), 122630(레버), QQQ.",
          {"code": {"type": "string"}, "days": {"type": "integer", "description": "기본 30"}}, ["code"]),
    # 운영 상태·로그 (2026-09-07, ADR-009 갱신) — 무인 실행·예수금 대조·알림 설정을 챗봇이 답할 수 있게
    _tool("auto_exec_status", "무인 운영 상태 — 계좌별 플래그(무인 매수/매도·하루 매수 상한), 포트별 상태 한 줄(수동/대기/완료/취소/정지/경고)과 사유, 마지막 09:01 실행 요약, 예수금 대조, 살아 있는 주문 수, 알림 설정 여부. '왜 발주가 안 됐나' 질문에 recent_logs 와 함께 사용.",
          {"portfolio_id": {"type": "integer", "description": "생략 시 국내 포트 전부"}}),
    _tool("recent_logs", "매매 로그 — 거래 원장·주문 상태·실행/동기화 이벤트를 최신순으로. 실패·경고만 보려면 level=warn 또는 error. '왜 발주가 안 됐나' 질문에 사용.",
          {"days": {"type": "integer", "description": "최근 N일 (기본 7)"}, "level": {"type": "string", "enum": ["all", "warn", "error"]},
           "type": {"type": "string", "enum": ["all", "trade", "order", "event"]}, "portfolio_id": {"type": "integer"}}),
]


def _auto_exec_status(session, user_id: int, pid) -> dict:
    """무인 운영 상태 요약 — 화면(주문표 패널·설정)과 같은 원천. 읽기 전용, user_id 스코프. 채팅 ID·토큰은 내보내지 않는다."""
    from sqlalchemy import select
    from app.autoexec import auto_exec_settings_view, auto_exec_view
    from app.cashcheck import pf_cash_check
    from app.dashboard import kst_today
    from app.models import BrokerOrder, TradePortfolio
    from app.notify import user_notify

    q = select(TradePortfolio).where(TradePortfolio.user_id == user_id, TradePortfolio.market == "KR")
    if pid:
        q = q.where(TradePortfolio.id == int(pid))
    pfs = session.scalars(q.order_by(TradePortfolio.id)).all()
    if pid and not pfs:
        return {"error": "portfolio not found"}
    today = kst_today()
    n = user_notify(session, user_id)
    sv = auto_exec_settings_view(session, user_id)
    out: dict = {"settings": {"default": sv["default"],
                              "accounts": [{"id": a["id"], "label": a["label"], "env": a["env"], "auto_exec": a["auto_exec"],
                                            "linked_portfolios": a["linked_portfolios"]} for a in sv["accounts"]]},
                 "notify": {"enabled": n["enabled"], "ready": n["ready"], "events": n["events"]},
                 "portfolios": []}
    for pf in pfs:
        live = session.scalars(select(BrokerOrder).where(
            BrokerOrder.portfolio_id == pf.id, BrokerOrder.plan_date >= today,
            BrokerOrder.status.in_(("submitted", "partial")))).all()
        view = auto_exec_view(session, pf)
        out["portfolios"].append({
            "portfolio_id": pf.id, "name": pf.name, "broker_linked": bool(pf.broker_credential_id),
            "account": view.get("account"), "allowed": view["allowed"],
            "exec_day": view["exec_day"], "state": view["state"],   # 화면 상단 상태 한 줄과 같은 원천 (ADR-009 §3)
            "paused": view["paused"], "paused_reason": view["paused_reason"], "fail_streak": view["fail_streak"],
            "last_run": view["last_run"], "skip": view["skip"], "cash_check": pf_cash_check(pf),
            "live_orders": {"count": len(live),
                            "by_status": {s: sum(1 for r in live if r.status == s) for s in ("submitted", "partial")}}})
    return out


def _run_tool(name: str, args: dict, user_id: int, is_admin: bool = False) -> dict:
    """도구 실행 — 전부 읽기 전용, user_id 스코프. 실패는 {'error': ...} 로 모델에 전달."""
    with SessionLocal() as session:
        try:
            if name == "list_portfolios":
                from app.portfolios import list_portfolios
                return list_portfolios(user_id=user_id, session=session)
            if name == "portfolio_summary":
                from app.portfolios import portfolio_summary
                out = portfolio_summary(portfolio_id=args.get("portfolio_id"),
                                        include_costs=True, user_id=user_id, session=session)
                # 필드 뜻 (2026-09-09): 누적 vs 오늘을 이름으로 구분 — 챗봇이 누적 평가손익을 '오늘 평가손익'으로 적은 혼동 방지
                out["fields_note"] = ("unrealized_total(=unrealized_pnl) 과 positions[].unrealized 은 매수 이후 **누적** 평가손익. "
                                      "day_change/day_change_pct 와 positions[].day_change 는 평가 종가일(day_change_asof) **하루** 손익(전 거래일 종가 prev_close 대비). "
                                      "realized_pnl 은 매도 실현 누적, net_pnl 은 실현+평가−비용.")
                return out
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
                    from app.signals import _live_us_model, _portfolio_orders, _us_portfolio_orders
                    pf_row = session.get(TradePortfolio, int(pid))
                    if pf_row is None or pf_row.user_id != user_id:
                        return {"error": "portfolio not found"}
                    if pf_row.market == "US":
                        base = _live_us_model(session, user_id)
                        if base.get("status") == "OK":
                            base.update(_us_portfolio_orders(session, pf_row, int(pid)))
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
                if not is_admin:  # 일반 권한: 파라미터 값 = 공식 유추 소재 (2026-09-05 권한 분리)
                    return {"error": "알고리즘 파라미터 조회는 관리자 전용입니다"}
                from app.settings import get_algo_settings
                return get_algo_settings(user_id=user_id, session=session)
            if name == "trading_journal":
                # 수동 매매일지 (2026-09-09 복구 — PR #86 이 journals_overview 를 지운 뒤 ImportError 로 조회 불가였다)
                from app.mjournal import filter_journal_rows, get_journal, journals_overview
                jid, q, days, limit = args.get("journal_id"), args.get("q"), args.get("days"), int(args.get("limit") or 50)
                if jid:
                    out = get_journal(int(jid), user_id=user_id, session=session)
                    out.pop("series", None)   # 차트용 시계열은 모델에 불필요
                    rows = filter_journal_rows(out.get("rows") or [], q, days)
                    out["rows_total"], out["rows"] = len(rows), rows[:limit]
                    return out
                return journals_overview(user_id=user_id, session=session, q=q, days=days, limit=limit)
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
            if name == "auto_exec_status":
                return _auto_exec_status(session, user_id, args.get("portfolio_id"))
            if name == "recent_logs":
                from app.activity import list_logs
                out = list_logs(days=int(args.get("days") or 7), portfolio_id=args.get("portfolio_id"),
                                type=str(args.get("type") or "all"), level=str(args.get("level") or "all"), q=None, limit=60,
                                user_id=user_id, session=session)
                return {"days": out["days"], "total": out["total"], "counts": out["counts"],
                        "items": [{k: i.get(k) for k in ("at", "type", "kind_ko", "level", "portfolio", "text", "detail")} for i in out["items"]]}
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
           "price_history": "시세 조회", "trading_journal": "매매일지",
           "auto_exec_status": "무인 운영 상태", "recent_logs": "매매 로그"}


@router.post("/chat")
def chat(body: ChatIn, user_id: int = Depends(current_user_id)) -> StreamingResponse:
    if not get_settings().openrouter_api_key:
        raise HTTPException(status_code=503,
                            detail="OPENROUTER_API_KEY 가 설정되지 않았습니다 — .env 에 키를 넣고 API 를 재시작하세요.")

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    # 프롬프트 조립 (2026-09-05 권한 분리): 본문 + [관리자: 전략 상세 | 일반: 개념+제한 계약]
    # + 코어 계약(고정) + 사용자 추가 지침
    with SessionLocal() as _s:
        from sqlalchemy import select
        from app.models import User, UserSettings
        body_text = _system_body(_s)
        _row = _s.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
        user_prompt = (_row.chat_prompt if _row else "") or ""
        _u = _s.get(User, user_id)
        is_admin = bool(_u and _u.is_admin)

    tools = TOOLS if is_admin else [t for t in TOOLS if t["function"]["name"] != "algorithm_params"]

    def stream():
        strategy = STRATEGY_DETAIL if is_admin else STRATEGY_PLAIN
        sys_text = body_text + "\n\n" + strategy + "\n\n" + OPERATIONS_KNOWLEDGE + "\n\n" + CORE_CONTRACT
        if not is_admin:
            sys_text += "\n\n" + RESTRICT_CONTRACT
        sys_text += f"\n오늘: {datetime.now(timezone(timedelta(hours=9))).date().isoformat()}"  # KST — UTC 날짜는 새벽에 하루 밀린다
        if user_prompt:
            sys_text += ("\n\n## 사용자 추가 지침 (시스템 계약·공개 제한과 충돌하면 그것들이 우선)\n" + user_prompt)
        msgs: list[dict] = [{"role": "system", "content": sys_text}]
        msgs += [m.model_dump() for m in body.messages]
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                data = _openrouter_call(msgs, tools)
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
                    result = _run_tool(fname, fargs, user_id, is_admin)
                    msgs.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": json.dumps(result, ensure_ascii=False, default=str)[:20000]})
            yield sse({"type": "final",
                       "content": "도구 호출이 너무 깊어져 중단했습니다 — 질문을 더 구체적으로 나눠주세요."})
        except Exception as e:  # noqa: BLE001 — 오류를 SSE 로 전달 (연결이 이미 200 이므로)
            log.warning("chat failed: %s", e)
            yield sse({"type": "error", "content": f"응답 생성 실패: {e}"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
