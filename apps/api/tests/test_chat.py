"""매매 도우미 챗봇 하네스 테스트 — OpenRouter 호출은 가짜로 대체 (2026-09-04).

검증: ① 키 미설정 503 ② 미인증 401 ③ 도구 루프(tool_calls → 도구 실행 → 최종 답) SSE
④ 도구가 실제 사용자 데이터로 스코프 ⑤ 도구 오류가 대화를 죽이지 않음.
"""
import json
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _events(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.split("\n\n") if line.strip().startswith("data:")]


def _authed_client() -> tuple[TestClient, dict]:
    client = TestClient(app, base_url="https://testserver")
    email = f"chat{uuid.uuid4().hex[:8]}@stocklab.dev"
    token = client.post("/auth/register", json={"email": email, "password": "password123"}).json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_chat_requires_auth_and_key(monkeypatch):
    client = TestClient(app, base_url="https://testserver")
    assert client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401

    client, headers = _authed_client()
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "")
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers)
    assert r.status_code == 503
    assert "OPENROUTER_API_KEY" in r.json()["detail"]


def test_chat_tool_loop_and_final(monkeypatch):
    client, headers = _authed_client()
    from app import chat as chat_mod
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")

    calls = {"n": 0, "tool_payload": None}

    def fake_call(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:  # 1회차: 포트 목록 도구 호출 요구
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "list_portfolios", "arguments": "{}"}}],
            }}]}
        # 2회차: 도구 결과가 대화에 들어왔는지 확인 후 최종 답
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"
        calls["tool_payload"] = json.loads(tool_msgs[0]["content"])
        return {"choices": [{"message": {"role": "assistant", "content": "포트가 없습니다."}}]}

    monkeypatch.setattr(chat_mod, "_openrouter_call", fake_call)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "내 포트 보여줘"}]}, headers=headers)
    assert r.status_code == 200
    evs = _events(r.text)
    assert [e["type"] for e in evs] == ["tool", "final"]
    assert evs[0]["name"] == "list_portfolios"
    assert evs[1]["content"] == "포트가 없습니다."
    # 새 계정이므로 도구 결과는 빈 목록 — 사용자 스코프 확인
    assert calls["tool_payload"] == {"items": []}


def test_chat_tool_error_returned_to_model(monkeypatch):
    client, headers = _authed_client()
    from app import chat as chat_mod
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")

    seen = {}

    def fake_call(messages, tools):
        if not any(m.get("role") == "tool" for m in messages):
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "price_history",
                                             "arguments": json.dumps({"code": "NOPE"})}}],
            }}]}
        seen["tool"] = json.loads([m for m in messages if m["role"] == "tool"][0]["content"])
        return {"choices": [{"message": {"role": "assistant", "content": "그 종목은 없습니다."}}]}

    monkeypatch.setattr(chat_mod, "_openrouter_call", fake_call)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "NOPE 시세"}]}, headers=headers)
    evs = _events(r.text)
    assert evs[-1]["type"] == "final"
    assert "error" in seen["tool"]  # 도구 실패가 에러 페이로드로 모델에 전달됨


def test_chat_upstream_failure_becomes_error_event(monkeypatch):
    client, headers = _authed_client()
    from app import chat as chat_mod
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")

    def boom(messages, tools):
        raise RuntimeError("OpenRouter 402: quota")

    monkeypatch.setattr(chat_mod, "_openrouter_call", boom)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers)
    evs = _events(r.text)
    assert evs[-1]["type"] == "error"
    assert "402" in evs[-1]["content"]
