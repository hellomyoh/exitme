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


def test_chat_prompt_setting_appended(monkeypatch):
    """일반 설정의 챗봇 추가 지침이 시스템 메시지 뒤에 덧붙는다 (2026-09-04)."""
    client, headers = _authed_client()
    from app import chat as chat_mod
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")

    # 저장/조회 왕복
    r = client.put("/settings/chat", json={"prompt": "답변 끝에 [끝] 을 붙여라"}, headers=headers)
    assert r.status_code == 200 and r.json()["saved"]
    assert client.get("/settings/chat", headers=headers).json()["prompt"] == "답변 끝에 [끝] 을 붙여라"

    seen = {}

    def fake_call(messages, tools):
        seen["system"] = messages[0]["content"]
        return {"choices": [{"message": {"role": "assistant", "content": "ok [끝]"}}]}

    monkeypatch.setattr(chat_mod, "_openrouter_call", fake_call)
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers)
    assert messages_contains(seen["system"])


def messages_contains(system_text: str) -> bool:
    return ("사용자 추가 지침" in system_text and "답변 끝에 [끝] 을 붙여라" in system_text
            and system_text.index("핵심만 간결하게") < system_text.index("사용자 추가 지침"))


def test_chat_system_prompt_admin_only_and_replace(monkeypatch):
    """전역 시스템 프롬프트 — 관리자 전용, 본문 교체 시에도 코어 계약·추가 지침은 유지 (2026-09-04)."""
    client, headers = _authed_client()  # 일반 사용자
    assert client.get("/settings/chat-system", headers=headers).status_code == 403
    assert client.put("/settings/chat-system", json={"prompt": "x"}, headers=headers).status_code == 403

    # 관리자 (부트스트랩 myoh)
    admin_tok = client.post("/auth/login", json={"email": "myoh", "password": "anrndghk!"}).json()["access_token"]
    ah = {"Authorization": f"Bearer {admin_tok}"}
    g = client.get("/settings/chat-system", headers=ah).json()
    assert g["prompt"] == "" and "ExitMe" in g["default"] and "시스템 계약" in g["core_contract"]

    assert client.put("/settings/chat-system", json={"prompt": "너는 해적처럼 말하는 도우미다."},
                      headers=ah).json()["using_default"] is False

    from app import chat as chat_mod
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    seen = {}

    def fake_call(messages, tools):
        seen["system"] = messages[0]["content"]
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    monkeypatch.setattr(chat_mod, "_openrouter_call", fake_call)
    # 일반 사용자 대화에도 전역 교체본 + 코어 계약 + (개인) 추가 지침이 함께 들어간다
    client.put("/settings/chat", json={"prompt": "존댓말로."}, headers=headers)
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers)
    sys_text = seen["system"]
    assert "해적처럼" in sys_text and "정본 요약" not in sys_text     # 본문 교체됨
    assert "시스템 계약" in sys_text and "읽기 전용" in sys_text      # 코어 계약 유지
    assert "존댓말로." in sys_text                                     # 추가 지침 유지

    # 초기화(빈 값) → 기본 복귀
    assert client.put("/settings/chat-system", json={"prompt": ""}, headers=ah).json()["using_default"] is True
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers)
    assert "정본 요약" in seen["system"] and "해적처럼" not in seen["system"]
