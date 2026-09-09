"""챗봇 운영 지식·도구 (2026-09-07 지시 "챗봇에서 관련 내용을 답변 가능한 상태인지 검토·보완") — auto_exec_status·recent_logs 도구, 프롬프트 지식."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]
KST = timezone(timedelta(hours=9))


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"co{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _uid(pid: int) -> int:
    from app.models import TradePortfolio

    with SessionLocal() as s:
        return s.get(TradePortfolio, pid).user_id


def test_prompt_knowledge_and_tool_registry():
    """프롬프트에 운영 기능 지식이 들어가고, 도구 목록·표시명이 일치한다."""
    from app.chat import CORE_CONTRACT, OPERATIONS_KNOWLEDGE, STRATEGY_DETAIL, STRATEGY_PLAIN, TOOL_KO, TOOLS

    names = {t["function"]["name"] for t in TOOLS}
    assert {"auto_exec_status", "recent_logs"} <= names and all(n in TOOL_KO for n in names)
    for kw in ("무인 매매", "09:01", "무인 취소", "예수금 대조", "매매 로그", "텔레그램", "auto_exec_status", "recent_logs", "다시 켜기"):
        assert kw in OPERATIONS_KNOWLEDGE, kw
    assert "매매 로그" in CORE_CONTRACT and "텔레그램 알림 = 일반 설정" in CORE_CONTRACT
    # 'HTS 에서만 발주' 라는 낡은 문장은 사라지고 세 경로가 적혀 있다
    assert "HTS 에서 직접 하고 결과만" not in STRATEGY_DETAIL and "무인 실행" in STRATEGY_DETAIL and "무인 실행" in STRATEGY_PLAIN


def test_auto_exec_status_and_recent_logs_tools_are_user_scoped():
    """auto_exec_status: 설정·포트별 상태·살아 있는 주문·알림 여부(채팅 ID 제외). recent_logs: 매매 로그 병합. 타인 포트는 error."""
    from app.chat import _run_tool
    from app.models import BrokerOrder

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "챗상태", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    uid = _uid(pid)
    today = datetime.now(KST).date()
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                               "executed_at": (today - timedelta(days=1)).isoformat() + "T15:30:00+09:00"}, headers=h)
    c.put("/settings/auto-exec", json={"buy": True, "sell": False}, headers=h)
    c.put("/settings/notify", json={"bot_token": "123456789:AAHxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456", "chat_id": "42", "enabled": True}, headers=h)
    with SessionLocal() as s:
        s.add(BrokerOrder(portfolio_id=pid, broker_credential_id=None, plan_date=today, line_key="grid1:K200:buy:limit:99000",
                          code="069500", instrument="K200", kind="grid1", side="buy", otype="limit", qty=5, price=99_000,
                          status="submitted", mode="auto", order_no="N1"))
        s.commit()
    st = _run_tool("auto_exec_status", {}, uid)
    assert st["settings"]["default"] == {"buy": True, "sell": False, "daily_buy_cap_pct": 0.0} and st["settings"]["accounts"] == []
    assert st["notify"]["enabled"] is True and st["notify"]["ready"] is True and "chat_id" not in st["notify"] and "token" not in str(st["notify"])
    p = next(x for x in st["portfolios"] if x["portfolio_id"] == pid)
    assert p["name"] == "챗상태" and p["broker_linked"] is False and p["paused"] is False
    assert p["state"]["code"] == "off" and "계좌 없음" in p["state"]["label"] and p["exec_day"] == today.isoformat()   # 계좌 미연결 = 수동 모드
    assert p["live_orders"] == {"count": 1, "by_status": {"submitted": 1, "partial": 0}}
    assert p["cash_check"] is None and p["skip"] is None
    one = _run_tool("auto_exec_status", {"portfolio_id": pid}, uid)
    assert [x["portfolio_id"] for x in one["portfolios"]] == [pid]
    logs = _run_tool("recent_logs", {"days": 7}, uid)
    assert logs["total"] >= 2 and {i["type"] for i in logs["items"]} >= {"trade", "order"}
    assert all(set(i) == {"at", "type", "kind_ko", "level", "portfolio", "text", "detail"} for i in logs["items"])
    assert _run_tool("recent_logs", {"days": 7, "level": "error"}, uid)["total"] == 0
    # 타인 계정
    c2, h2 = _client()
    pid2 = c2.post("/portfolios", json={"name": "남", "market": "KR"}, headers=h2).json()["id"]
    uid2 = _uid(pid2)
    assert _run_tool("auto_exec_status", {"portfolio_id": pid}, uid2) == {"error": "portfolio not found"}
    assert all(x["portfolio_id"] != pid for x in _run_tool("auto_exec_status", {}, uid2)["portfolios"])
    assert "error" in _run_tool("recent_logs", {"portfolio_id": pid}, uid2)


def test_trading_journal_tool_overview_search_detail_and_scope(monkeypatch):
    """수동 매매일지 도구 복구 (2026-09-09 사용자 보고 "챗봇에서 매매일지 검색이 안 된다") — PR #86 이 journals_overview 를 지운 뒤
    ImportError 로 실패했다. 전체 요약 + 최근 기록, q/days 검색, journal_id 상세(series 제외·rows 제한), 다른 사용자 스코프."""
    import app.mjournal as mj
    from app.chat import _run_tool
    from app.models import ManualJournal

    class _NoKis:   # 상세 조회의 시세 보충이 실 KIS 로 나가 공유 CI DB 에 005930 일봉을 적재하지 않게 (test_mjournal 평가 테스트 오염 방지)
        def fetch_daily(self, code, a, b, org_price=True):
            return []

    monkeypatch.setattr(mj, "_kis_for_bars", lambda session, j: _NoKis())

    c, h = _client()
    jid = c.post("/mjournals", json={"name": "국내주식", "symbol": "삼성전자", "broker": "한투"}, headers=h).json()["id"]
    jid2 = c.post("/mjournals", json={"name": "미국주식", "symbol": "AAPL"}, headers=h).json()["id"]
    today = datetime.now(KST).date()
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 10, "price": 70_000, "trade_date": (today - timedelta(days=40)).isoformat(),
                                             "reason": "분할 매수", "code": "005930"}, headers=h)
    c.post(f"/mjournals/{jid}/entries", json={"side": "sell", "qty": 4, "price": 80_000, "trade_date": (today - timedelta(days=3)).isoformat(),
                                             "reason": "일부 익절", "code": "005930"}, headers=h)
    c.post(f"/mjournals/{jid}/entries", json={"side": "buy", "qty": 2, "price": 500_000, "trade_date": (today - timedelta(days=2)).isoformat(),
                                             "symbol": "현대차"}, headers=h)
    c.post(f"/mjournals/{jid2}/entries", json={"side": "buy", "qty": 1, "price": 200, "trade_date": today.isoformat()}, headers=h)
    with SessionLocal() as s:
        uid = s.get(ManualJournal, jid).user_id
    # 전체 요약 — 일지 2개, 보유(삼성전자 6주·현대차 2주·AAPL 1주), 기록 최신순
    out = _run_tool("trading_journal", {}, uid)
    assert "error" not in out and [j["name"] for j in out["journals"]] == ["국내주식", "미국주식"]
    kr = out["journals"][0]
    assert {(x["symbol"], x["qty"]) for x in kr["holdings"]} == {("삼성전자", 6), ("현대차", 2)} and kr["entries"] == 3 and kr["summary"]["realized"] > 0
    assert out["entries_total"] == 4 and out["entries"][0]["journal"] == "미국주식" and out["entries"][1]["symbol"] == "현대차"
    # 검색 — 종목명·코드·일지 이름, 최근 N일
    q = _run_tool("trading_journal", {"q": "삼성"}, uid)
    assert [j["name"] for j in q["journals"]] == ["국내주식"] and [e["side"] for e in q["entries"]] == ["sell", "buy"] and all(e["symbol"] == "삼성전자" for e in q["entries"])
    assert _run_tool("trading_journal", {"q": "005930"}, uid)["entries_total"] == 2
    assert [e["symbol"] for e in _run_tool("trading_journal", {"q": "미국"}, uid)["entries"]] == ["AAPL"]
    assert _run_tool("trading_journal", {"days": 7}, uid)["entries_total"] == 3
    assert _run_tool("trading_journal", {"q": "없는종목"}, uid) == {**_run_tool("trading_journal", {"q": "없는종목"}, uid), "journals": [], "entries": []}
    # 상세 — series 없음, q·limit 로 rows 제한
    d = _run_tool("trading_journal", {"journal_id": jid}, uid)
    assert d["name"] == "국내주식" and "series" not in d and d["rows_total"] == 3 and len(d["rows"]) == 3
    d2 = _run_tool("trading_journal", {"journal_id": jid, "q": "삼성전자", "limit": 1}, uid)
    assert d2["rows_total"] == 2 and len(d2["rows"]) == 1 and d2["rows"][0]["side"] == "sell"
    # 다른 사용자 — 목록 비어 있고 상세는 오류
    c2, _h2 = _client()
    jid_other = c2.post("/mjournals", json={"name": "남의일지", "symbol": "X"}, headers=_h2).json()["id"]
    with SessionLocal() as s:
        uid2 = s.get(ManualJournal, jid_other).user_id
    assert [j["name"] for j in _run_tool("trading_journal", {}, uid2)["journals"]] == ["남의일지"]
    assert "error" in _run_tool("trading_journal", {"journal_id": jid}, uid2)
