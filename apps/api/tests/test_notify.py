"""텔레그램 알림 (2026-09-07 지시) — 설정(마스킹·검증)·이벤트 카테고리 필터·실패 로그·거래 등록 알림·연결 확인(chat_id 자동)·일일 현황."""
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
TOKEN = "123456789:AAHxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"


def _client():
    c = TestClient(app, base_url="https://testserver")
    tok = c.post("/auth/register", json={"email": f"tg{uuid.uuid4().hex[:8]}@x.dev", "password": "password123"}).json()["access_token"]
    return c, {"Authorization": f"Bearer {tok}"}


def _uid(pid: int) -> int:
    from app.models import TradePortfolio

    with SessionLocal() as s:
        return s.get(TradePortfolio, pid).user_id


def _capture(monkeypatch):
    import app.notify as nt

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(nt, "_http_send", lambda token, chat_id, text: sent.append((token, chat_id, text)) or {"ok": True})
    return sent


def test_notify_settings_roundtrip_and_validation():
    """기본값(꺼짐·토큰 없음·항목 7개 — 자동 승인·사전 갭 취소는 ADR-009 로 폐지), 토큰 형식 검증, 마스킹, 항목 병합, 끄기·토큰 삭제."""
    c, h = _client()
    g = c.get("/settings/notify", headers=h).json()
    assert g["enabled"] is False and g["has_token"] is False and g["ready"] is False and len(g["categories"]) == 7
    assert g["events"]["autoexec"] is True and g["events"]["orders"] is False and g["events"]["trades"] is False and g["events"]["daily_status"] is True
    r = c.put("/settings/notify", json={"bot_token": "not-a-token"}, headers=h)
    assert r.status_code == 422 and "토큰 형식" in r.json()["detail"]
    assert c.put("/settings/notify", json={"chat_id": "abc"}, headers=h).status_code == 422
    j = c.put("/settings/notify", json={"bot_token": TOKEN, "chat_id": "12345", "enabled": True, "events": {"orders": True, "bogus": True}}, headers=h).json()
    assert j["has_token"] is True and j["token_masked"].startswith("123456") and "*" in j["token_masked"] and TOKEN not in j["token_masked"]
    assert j["chat_id"] == "12345" and j["enabled"] is True and j["ready"] is True and j["events"]["orders"] is True and "bogus" not in j["events"]
    g = c.get("/settings/notify", headers=h).json()
    assert g["ready"] is True and g["events"]["autoexec"] is True
    # 빈 토큰은 유지, 끄면 ready 아님, 토큰 삭제
    put = c.put("/settings/notify", json={"bot_token": "", "enabled": False}, headers=h).json()
    g = c.get("/settings/notify", headers=h).json()
    assert put == {k: v for k, v in g.items() if k != "categories"}
    assert g["has_token"] is True and g["ready"] is False
    assert c.put("/settings/notify", json={"clear_token": True}, headers=h).json()["has_token"] is False
    # 다른 사용자에게는 보이지 않는다
    _, h2 = _client()
    assert c.get("/settings/notify", headers=h2).json()["chat_id"] == ""


def test_event_hook_filters_by_category_and_logs_failures(monkeypatch):
    """활동 로그 → 카테고리 매핑 → 체크된 항목만 전송. 수준별 머리말, 미매핑 종류·꺼진 항목·알림 꺼짐은 미전송. 전송 실패는 로그로."""
    import app.notify as nt
    from app.activity import log_event

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "알림포트", "market": "KR"}, headers=h).json()["id"]
    uid = _uid(pid)
    c.put("/settings/notify", json={"bot_token": TOKEN, "chat_id": "12345", "enabled": True}, headers=h)
    sent = _capture(monkeypatch)
    with SessionLocal() as s:
        log_event(s, uid, "autoexec.run", "무인 실행 09:01 — 발주 2건", portfolio_id=pid)
        log_event(s, uid, "autoexec.paused", "무인 실행 정지 — 발주 연속 실패 2회", level="error", portfolio_id=pid)
        log_event(s, uid, "order.reserve", "예약주문 접수 2건", portfolio_id=pid)      # orders 기본 꺼짐
        log_event(s, uid, "preopen.cancel", "그리드 매수 취소 — …", level="warn", portfolio_id=pid)   # 줄 단위 — 매핑 없음
        s.commit()
    assert len(sent) == 2
    assert sent[0][0] == TOKEN and sent[0][1] == "12345" and sent[0][2].startswith("ℹ️ [ExitMe · 알림포트] 무인 실행 09:01")
    assert sent[1][2].startswith("🛑 [ExitMe · 알림포트] 무인 실행 정지")
    # 항목을 켜면 온다
    c.put("/settings/notify", json={"events": {"orders": True}}, headers=h)
    with SessionLocal() as s:
        log_event(s, uid, "order.reserve", "예약주문 접수 1건", portfolio_id=pid)
        s.commit()
    assert len(sent) == 3 and "예약주문 접수 1건" in sent[2][2]
    # 알림 꺼짐 → 미전송
    c.put("/settings/notify", json={"enabled": False}, headers=h)
    with SessionLocal() as s:
        log_event(s, uid, "autoexec.run", "무인 실행 — 발주 1건", portfolio_id=pid)
        s.commit()
    assert len(sent) == 3
    # 전송 실패 → 예외 없이 로그(notify.failed)만
    c.put("/settings/notify", json={"enabled": True}, headers=h)

    def boom(token, chat_id, text):
        raise RuntimeError("telegram 401: Unauthorized")
    monkeypatch.setattr(nt, "_http_send", boom)
    with SessionLocal() as s:
        log_event(s, uid, "autoexec.run", "무인 실행 — 발주 1건", portfolio_id=pid)
        s.commit()
    ev = [i for i in c.get("/logs?type=event&level=warn", headers=h).json()["items"] if i["kind"] == "notify.failed"]
    assert len(ev) == 1 and "올바르지 않습니다" in ev[0]["text"]
    # 설정 화면용 마지막 결과 — 성공 시각은 남고 실패 사유·시각이 붙는다 (2026-09-09)
    last = c.get("/settings/notify", headers=h).json()["last"]
    assert last["sent_at"] and "올바르지 않습니다" in last["error"] and last["error_at"]
    # 연결 오류는 사람 말로
    assert "연결할 수 없습니다" in nt._humanize(RuntimeError("HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded (Caused by SSLError(SSLEOFError(8, 'EOF occurred')))"))


def test_trade_register_and_delete_notify(monkeypatch):
    """'체결 등록' 항목을 켠 사용자: 수동 입금·매수 등록과 거래 삭제가 전송된다."""
    c, h = _client()
    pid = c.post("/portfolios", json={"name": "거래알림", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    c.put("/settings/notify", json={"bot_token": TOKEN, "chat_id": "5", "enabled": True, "events": {"trades": True}}, headers=h)
    sent = _capture(monkeypatch)
    d = (datetime.now(KST).date() - timedelta(days=2)).isoformat()
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000, "executed_at": d + "T15:30:00+09:00"}, headers=h)
    tx = c.post("/positions", json={"portfolio_id": pid, "kind": "buy", "code": "069500", "qty": 5, "price": 100_000,
                                    "executed_at": d + "T15:31:00+09:00", "memo": "테스트"}, headers=h).json()["id"]
    assert [t[2] for t in sent] == ["ℹ️ [ExitMe · 거래알림] 입금 등록 — 1,000,000원",
                                    "ℹ️ [ExitMe · 거래알림] 체결 등록 — 매수 KODEX 200 5주 @100,000원 · 테스트"]
    assert c.delete(f"/positions/{tx}", headers=h).status_code == 200
    assert len(sent) == 3 and "거래 삭제 — 매수" in sent[2][2] and sent[2][2].startswith("⚠️")
    # 항목을 끄면 오지 않는다
    c.put("/settings/notify", json={"events": {"trades": False}}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 10_000, "executed_at": d + "T15:32:00+09:00"}, headers=h)
    assert len(sent) == 3


def test_connection_check_resolves_chat_id(monkeypatch):
    """연결 확인: 토큰 없음 409 → 봇에 말 안 걸었으면 409 안내 → getUpdates 로 chat_id 저장 + 테스트 메시지 → 토큰 오류 502."""
    import app.notify as nt

    c, h = _client()
    assert c.post("/settings/notify/test", headers=h).status_code == 409
    c.put("/settings/notify", json={"bot_token": TOKEN}, headers=h)
    sent = _capture(monkeypatch)
    monkeypatch.setattr(nt, "_http_get_updates", lambda token: [])
    r = c.post("/settings/notify/test", headers=h)
    assert r.status_code == 409 and "아무 메시지" in r.json()["detail"]
    monkeypatch.setattr(nt, "_http_get_updates", lambda token: [
        {"update_id": 1, "message": {"chat": {"id": 111, "first_name": "Old"}}},
        {"update_id": 2, "message": {"chat": {"id": 777, "first_name": "Mun", "username": "mun"}}}])
    j = c.post("/settings/notify/test", headers=h).json()
    assert j["ok"] is True and j["chat_id"] == "777" and "Mun" in j["chat_title"]
    assert c.get("/settings/notify", headers=h).json()["chat_id"] == "777"
    assert len(sent) == 1 and sent[0][1] == "777" and "연결 확인" in sent[0][2]

    def bad(token):
        raise RuntimeError("telegram 401: Unauthorized")
    c.put("/settings/notify", json={"chat_id": ""}, headers=h)
    monkeypatch.setattr(nt, "_http_get_updates", bad)
    r = c.post("/settings/notify/test", headers=h)
    assert r.status_code == 502 and "올바르지 않습니다" in r.json()["detail"]


def test_daily_status_text_and_send(monkeypatch):
    """16:40 일일 현황 — 총자산·구성·포트별 평가액. 항목이 켜진 사용자에게만 전송."""
    import app.notify as nt
    from app.dashboard import compute_user_snapshot, kst_today

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "현황포트", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    uid = _uid(pid)
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 3_000_000,
                               "executed_at": (kst_today() - timedelta(days=1)).isoformat() + "T15:30:00+09:00"}, headers=h)
    with SessionLocal() as s:
        compute_user_snapshot(s, uid, kst_today())
        s.commit()
        text = nt.daily_status_text(s, uid, kst_today())
    assert text is not None and text.startswith("📊 일일 현황") and "총자산 3,000,000원" in text and "· 현황포트: 3,000,000원" in text
    sent = _capture(monkeypatch)
    with SessionLocal() as s:
        assert nt.send_daily_status(s, uid, kst_today()) is False          # 알림 미설정
    c.put("/settings/notify", json={"bot_token": TOKEN, "chat_id": "9", "enabled": True}, headers=h)
    with SessionLocal() as s:
        assert nt.send_daily_status(s, uid, kst_today()) is True
    assert len(sent) == 1 and "일일 현황" in sent[0][2]
    c.put("/settings/notify", json={"events": {"daily_status": False}}, headers=h)
    with SessionLocal() as s:
        assert nt.send_daily_status(s, uid, kst_today()) is False


def test_connection_check_turns_notifications_on(monkeypatch):
    """운영 사례(2026-09-09): 토큰·채팅 ID·항목은 있는데 enabled 키가 없어 한 통도 안 감 → 연결 확인 성공 시 켠다."""
    c, h = _client()
    c.put("/settings/notify", json={"bot_token": TOKEN, "chat_id": "8580122820", "events": {"autoexec": True}}, headers=h)
    g = c.get("/settings/notify", headers=h).json()
    assert g["enabled"] is False and g["ready"] is False and g["chat_id"] == "8580122820"
    sent = _capture(monkeypatch)
    r = c.post("/settings/notify/test", headers=h).json()
    assert r["ok"] is True and r["enabled_now"] is True and len(sent) == 1
    g2 = c.get("/settings/notify", headers=h).json()
    assert g2["enabled"] is True and g2["ready"] is True and g2["last"]["sent_at"]


def test_daily_status_change_excludes_cash_flows():
    """일일 현황 '전일 대비'는 대시보드와 같이 입출금을 뺀 순수 성과 (2026-09-09 통일) — 오늘 1,000,000 입금 → 전일 대비 +0원, 입출금 표기."""
    import app.notify as nt
    from app.dashboard import compute_user_snapshot, kst_today
    from app.models import AssetSnapshot

    c, h = _client()
    pid = c.post("/portfolios", json={"name": "흐름포트", "market": "KR", "code_200": "069500"}, headers=h).json()["id"]
    uid = _uid(pid)
    today = kst_today()
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 3_000_000,
                               "executed_at": (today - timedelta(days=2)).isoformat() + "T15:30:00+09:00"}, headers=h)
    c.post("/positions", json={"portfolio_id": pid, "kind": "deposit", "amount": 1_000_000,
                               "executed_at": today.isoformat() + "T10:00:00+09:00"}, headers=h)
    with SessionLocal() as s:
        s.add(AssetSnapshot(user_id=uid, snap_date=today - timedelta(days=1), total=3_000_000, stock=0, cash=3_000_000, other=0))
        s.commit()
        compute_user_snapshot(s, uid, today)
        s.commit()
        text = nt.daily_status_text(s, uid, today)
    assert "총자산 4,000,000원" in text and "(전일 대비 +0원, +0.00% · 입출금 +1,000,000원 제외)" in text
