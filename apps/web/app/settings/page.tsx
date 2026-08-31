"use client";

/** 일반 설정 — 비밀번호 변경·세션·로그아웃 (2026-08-31 지시). */
import { useState } from "react";
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
    if (res.ok) { setMsg("✅ 변경되었습니다 — 다음 로그인부터 새 비밀번호를 사용하세요"); setCur(""); setPw1(""); setPw2(""); }
    else setMsg(((await res.json()) as { detail?: string }).detail ?? `변경 실패 (${res.status})`);
  }

  function logout() {
    void apiLogout().then(() => router.push("/login"));
  }

  return (
    <main>
      <PageTitle title="일반 설정" sub="계정·세션 관리" />
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
          로그인 세션은 <b className="text-ink">마지막 활동 후 1시간</b> 유지됩니다(활동 시 자동 연장).
          만료되면 로그인 화면으로 안내됩니다.
        </p>
        <button className="btn" onClick={logout}>로그아웃</button>
      </Card>
      <Callout icon="ℹ️">
        아이디(이메일) 변경은 지원하지 않습니다 — 새 계정을 만들어 사용하세요.
        알고리즘 상수는 <a href="/settings/algorithm" className="font-semibold text-accent">알고리즘 설정</a>에서 변경합니다.
      </Callout>
    </main>
  );
}
