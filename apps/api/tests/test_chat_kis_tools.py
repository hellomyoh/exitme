"""챗봇 KIS 부가 정보 도구 3개 (2026-09-15 지시, docs/chat-kis-context-tools-review-20260915.md).

market_news · investor_flow · market_index — 읽기 전용, 전역 시세 키, Redis 캐시, 09:01 실행 중 양보, 한국어 키.
KIS 는 가짜 클라이언트로 대체한다(실호출 없음). 캐시 적중 검사는 Redis 가 있어야 의미가 있어 integration 표시.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app import chat as chat_mod
from app.db import engine

try:
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_UP, reason="database not reachable")]


class _FakeKis:
    """실호출 프로브(2026-09-15)에서 본 응답 형태를 그대로 흉내 낸다."""

    def __init__(self):
        self.calls: list[tuple] = []

    def fetch_news_titles(self, code=None, limit=20):
        self.calls.append(("news", code, limit))
        return [{"date": "20260915", "time": "174304", "title": "미래에셋자산운용(주) ETF 추가 ㆍ 변경상장신청서",
                 "category_code": "04", "provider_code": "F", "codes": ["102110"]},
                {"date": "20260913", "time": "184023", "title": '"지수 불안할땐 월배당이 최고" 커버드콜 ETF에 몰린 개미들',
                 "category_code": "01", "provider_code": "C", "codes": []},
                {"date": "20260912", "time": "090000", "title": "분류 미상", "category_code": "07", "provider_code": "C", "codes": []}]

    def fetch_investor_flow(self, code, days=10):
        self.calls.append(("investor", code, days))
        return [{"date": "20260915", "close": 104650, "change": -1175, "person_net_qty": 59373,
                 "foreign_net_qty": 231504, "org_net_qty": -290877, "person_net_amt": 6_200_000_000,
                 "foreign_net_amt": 24_000_000_000, "org_net_amt": -30_000_000_000},
                {"date": "20260914", "close": 105825, "change": -3910, "person_net_qty": 131017,
                 "foreign_net_qty": 24884, "org_net_qty": -155901, "person_net_amt": 1, "foreign_net_amt": 2, "org_net_amt": -3}]

    def fetch_index_price(self, code="2001"):
        self.calls.append(("index", code))
        return {"code": code, "price": 1042.46, "change": -8.37, "change_pct": -0.80, "open": 1050.1,
                "high": 1052.3, "low": 1040.0, "volume": 100828, "advancing": 49, "declining": 149}


@pytest.fixture
def fake_kis(monkeypatch):
    fk = _FakeKis()
    monkeypatch.setattr(chat_mod, "_kis_market_client", lambda: fk)
    monkeypatch.setattr("app.autoexec.is_running", lambda: False)
    return fk


def _uid() -> str:
    return uuid.uuid4().hex[:6]


def test_market_news_returns_korean_keys_titles_only_and_caps_limit(fake_kis):
    out = chat_mod._run_tool("market_news", {"code": "N" + _uid(), "limit": 2}, 1)
    assert "error" not in out, out
    assert out["건수"] == 2 and len(out["items"]) == 2          # limit 상한 적용
    first = out["items"][0]
    assert set(first) == {"일시", "제목", "분류", "종목"}          # 한국어 키만
    assert first["일시"] == "2026-09-15 17:43" and first["분류"] == "공시" and first["종목"] == ["102110"]
    assert out["items"][1]["분류"] == "뉴스"
    assert "제목만" in out["참고"] and "단정하지 말 것" in out["참고"]   # 환각 방지 문구가 결과에 붙는다
    assert fake_kis.calls and fake_kis.calls[0][0] == "news"


def test_market_news_unknown_category_code_is_passed_through(fake_kis):
    out = chat_mod._run_tool("market_news", {"code": "N" + _uid(), "limit": 3}, 1)
    assert out["items"][2]["분류"] == "07"      # 표본 밖 코드는 지어내지 않고 그대로


def test_investor_flow_requires_code_and_uses_korean_keys(fake_kis):
    assert "error" in chat_mod._run_tool("investor_flow", {}, 1)
    out = chat_mod._run_tool("investor_flow", {"code": "I" + _uid(), "days": 1}, 1)
    assert out["일수"] == 1
    row = out["items"][0]
    assert row["일자"] == "2026-09-15" and row["종가"] == 104650 and row["외국인_순매수_수량"] == 231504
    assert row["기관_순매수_수량"] == -290877
    assert "순매수 = 매수 − 매도" in out["참고"]


def test_market_index_defaults_to_kospi200(fake_kis):
    out = chat_mod._run_tool("market_index", {}, 1)
    assert out["지수명"] == "KOSPI200" and out["현재지수"] == 1042.46 and out["등락률_pct"] == -0.80
    assert out["상승종목수"] == 49 and out["하락종목수"] == 149
    assert fake_kis.calls[-1] == ("index", "2001")


def test_second_ask_is_served_from_cache_without_calling_kis(fake_kis):
    code = "C" + _uid()
    a = chat_mod._run_tool("investor_flow", {"code": code, "days": 2}, 1)
    b = chat_mod._run_tool("investor_flow", {"code": code, "days": 2}, 1)
    assert a["cached"] is False and b["cached"] is True
    assert sum(1 for c in fake_kis.calls if c[0] == "investor") == 1, fake_kis.calls


def test_tools_yield_while_the_0901_executor_is_running(fake_kis, monkeypatch):
    monkeypatch.setattr("app.autoexec.is_running", lambda: True)
    out = chat_mod._run_tool("market_index", {}, 1)
    assert "error" in out and "무인 실행 중" in out["error"]
    assert not fake_kis.calls                                  # KIS 를 부르지 않았다


def test_kis_failure_surfaces_as_error_not_exception(monkeypatch):
    class _Broken:
        def fetch_index_price(self, code="2001"):
            from app.services.kis_client import KisError
            raise KisError("KIS error EGW00201 초당 거래건수를 초과하였습니다")

    monkeypatch.setattr(chat_mod, "_kis_market_client", lambda: _Broken())
    monkeypatch.setattr("app.autoexec.is_running", lambda: False)
    out = chat_mod._run_tool("market_index", {"code": "X" + _uid()}, 1)
    assert "error" in out and "EGW00201" in out["error"]


def test_missing_kis_keys_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(chat_mod, "_kis_market_client", lambda: None)
    monkeypatch.setattr("app.autoexec.is_running", lambda: False)
    out = chat_mod._run_tool("market_news", {}, 1)
    assert "error" in out and "KIS 시세 키" in out["error"]


def test_tools_are_offered_to_ordinary_users_and_model_loop_works(fake_kis, monkeypatch):
    """일반 권한에도 세 도구가 보이고(공식 유추 소재가 아님), 모델이 market_index 를 부르는 루프가 끝까지 돈다."""
    from app.config import get_settings
    from tests.test_chat import _authed_client, _events

    client, headers = _authed_client()
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    seen = {"tools": None, "n": 0}

    def fake_call(messages, tools):
        seen["n"] += 1
        seen["tools"] = [t["function"]["name"] for t in tools]
        if seen["n"] == 1:
            return {"choices": [{"message": {"role": "assistant", "content": None,
                                             "tool_calls": [{"id": "k1", "type": "function",
                                                             "function": {"name": "market_index", "arguments": "{}"}}]}}]}
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        payload = json.loads(tool_msgs[0]["content"])
        assert payload["지수명"] == "KOSPI200"
        return {"choices": [{"message": {"role": "assistant", "content": "KOSPI200 은 1042.46, −0.80% 입니다."}}]}

    monkeypatch.setattr(chat_mod, "_openrouter_call", fake_call)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "오늘 지수 어때"}]}, headers=headers)
    assert r.status_code == 200
    evs = _events(r.text)
    assert [e["type"] for e in evs] == ["tool", "final"] and evs[0]["name"] == "market_index"
    assert {"market_news", "investor_flow", "market_index"} <= set(seen["tools"])   # 일반 계정에도 노출
    assert "algorithm_params" not in seen["tools"]                                   # 기존 제한은 그대로
