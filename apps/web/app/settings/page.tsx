"use client";

/** 일반 설정 — 비밀번호 변경·세션·로그아웃 (2026-08-31 지시). */
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch, logout as apiLogout } from "../../lib/api";
import { Callout, Card, CardTitle, PageTitle } from "../../components/ui";

export default function SettingsPage() {
  const router = useRouter();
  const [cur, setCur] = useState("");
  const [pw1, setPw1] = useState("");
  const [pw2, setPw2] = useState("");
  const [msg, setMsg] = useState("");

  async function changePw() {
    setMsg("");
    if (pw1.length < 8) { setMsg("새 비밀번호는 8자 이상이어야 합니다"); return; }
    if (pw1 !== pw2) { setMsg("새 비밀번호 확인이 일치하지 않습니다"); return; }
    const res = await apiFetch("/auth/change-password", {
      method: "POST", body: JSON.stringify({ current_password: cur, new_password: pw1 }),
    });
    if (res.ok) {
      setMsg("✅ 변경되었습니다");
      setCur(""); setPw1(""); setPw2("");
      if (new URLSearchParams(window.location.search).get("force_pw") === "1") router.push("/dashboard");
    }
    else setMsg(((await res.json()) as { detail?: string }).detail ?? `변경 실패 (${res.status})`);
  }

  function logout() {
    void apiLogout().then(() => router.push("/login"));
  }

  // 챗봇 추가 지침 (2026-09-04) — 내장 프롬프트 뒤에 덧붙음
  const [chatPrompt, setChatPrompt] = useState("");
  const [chatMsg, setChatMsg] = useState("");
  useEffect(() => {
    void apiFetch("/settings/chat").then(async (r) => {
      if (r.ok) setChatPrompt(((await r.json()) as { prompt: string }).prompt);
    });
  }, []);
  async function saveChatPrompt() {
    const r = await apiFetch("/settings/chat", { method: "PUT", body: JSON.stringify({ prompt: chatPrompt }) });
    setChatMsg(r.ok ? "✅ 저장되었습니다 — 다음 대화부터 적용" : `저장 실패 (${r.status})`);
  }

  const forcePw = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("force_pw") === "1";
  return (
    <main>
      <PageTitle title="일반 설정" sub="계정·세션 관리" />
      {forcePw && (
        <Callout icon="🔐">
          <b className="text-ink">첫 로그인입니다 — 비밀번호를 변경해야 다른 메뉴를 사용할 수 있습니다.</b>{" "}
          아래에서 현재(임시) 비밀번호와 새 비밀번호를 입력하세요.
        </Callout>
      )}
      <div className="mb-4" />
      <Card className="mb-4 max-w-xl">
        <CardTitle>비밀번호 변경</CardTitle>
        <div className="grid max-w-sm gap-3">
          <label className="grid gap-1 text-[13px] text-faint">현재 비밀번호
            <input type="password" className="input" value={cur} onChange={(e) => setCur(e.target.value)} /></label>
          <label className="grid gap-1 text-[13px] text-faint">새 비밀번호 (8자 이상)
            <input type="password" className="input" value={pw1} onChange={(e) => setPw1(e.target.value)} /></label>
          <label className="grid gap-1 text-[13px] text-faint">새 비밀번호 확인
            <input type="password" className="input" value={pw2} onChange={(e) => setPw2(e.target.value)} /></label>
          <div className="flex items-center gap-3">
            <button className="btn btn-primary" onClick={() => void changePw()}>변경</button>
            {msg && <span className="text-[13.5px] text-muted">{msg}</span>}
          </div>
        </div>
      </Card>
      <Card className="mb-4 max-w-xl">
        <CardTitle>세션</CardTitle>
        <p className="mb-3 text-[14px] leading-relaxed text-muted">
          로그인 세션은 <b className="text-ink">마지막 활동 후 3시간</b> 유지됩니다(활동 시 자동 연장).
          만료되면 로그인 화면으로 안내됩니다.
        </p>
        <button className="btn" onClick={logout}>로그아웃</button>
      </Card>
      <Card className="mb-4 max-w-xl">
        <CardTitle>챗봇 추가 지침</CardTitle>
        <p className="mb-2 text-[13.5px] leading-relaxed text-muted">
          매매 도우미의 기본 지침(전략 지식·데이터 근거 규칙) <b className="text-ink">뒤에 덧붙는</b> 나만의 지침입니다.
          말투·관심 포트·보고 형식 등을 지정하세요. 기본 안전 규칙은 대체되지 않습니다.
        </p>
        <textarea rows={5} maxLength={4000} className="input w-full resize-y text-[13.5px]"
          placeholder={"예: 답변 끝에 오늘의 노출 E 를 항상 요약해줘.\n예: 메리츠자산 포트를 기본으로 다뤄줘."}
          value={chatPrompt} onChange={(e) => setChatPrompt(e.target.value)} />
        <div className="mt-2 flex items-center gap-3">
          <button className="btn btn-primary" onClick={() => void saveChatPrompt()}>저장</button>
          <span className="text-[12.5px] text-faint">{chatPrompt.length}/4000</span>
          {chatMsg && <span className="text-[13.5px] text-muted">{chatMsg}</span>}
        </div>
      </Card>
      <Callout icon="ℹ️">
        아이디(이메일) 변경은 지원하지 않습니다 — 새 계정을 만들어 사용하세요.
        알고리즘 상수는 <Link href="/settings/algorithm" className="font-semibold text-accent">알고리즘 설정</Link>에서 변경합니다.
      </Callout>
    </main>
  );
}
