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
    assert st["settings"]["default"] == {"buy": True, "sell": False, "daily_buy_cap_pct": 20.0} and st["settings"]["accounts"] == []
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
