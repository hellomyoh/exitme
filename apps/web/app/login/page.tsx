"use client";

/** 로그인/가입 — access는 메모리, refresh는 httpOnly 쿠키 (ADR-003). */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "../../lib/api";
import { Card } from "../../components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState(() => {
    try {
      return typeof window !== "undefined" && new URLSearchParams(window.location.search).get("expired")
        ? "세션이 만료되었습니다 (1시간 미사용) — 다시 로그인하세요" : "";
    } catch { return ""; }
  });
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setMsg("");
    const r = await login(email, password);
    setBusy(false);
    if (!r.ok) { setMsg("로그인 실패 — 아이디/비밀번호를 확인하세요"); return; }
    // 발급 계정 첫 로그인 — 비밀번호 변경 강제 (2026-09-01 지시)
    router.push(r.mustChangePassword ? "/settings?force_pw=1" : "/dashboard");
  }

  return (
    <main className="flex w-full items-center justify-center">
      <Card className="w-full max-w-sm !p-8">
        <div className="mb-6 flex items-center gap-2">
          <span className="inline-block h-3 w-3 rounded-sm bg-accent" />
          <h1 className="text-lg font-extrabold tracking-tight">ExitMe</h1>
        </div>
        <div className="grid gap-3">
          <label className="grid gap-1.5 text-xs text-faint">아이디
            <input className="input" value={email}
              onChange={(e) => setEmail(e.target.value)} /></label>
          <label className="grid gap-1.5 text-xs text-faint">비밀번호
            <input className="input" type="password" value={password}
              onKeyDown={(e) => e.key === "Enter" && void submit()}
              onChange={(e) => setPassword(e.target.value)} /></label>
          <button className="btn btn-primary mt-1" disabled={busy} onClick={() => void submit()}>로그인</button>
          {msg && <p className="text-[13px] text-up">{msg}</p>}
        </div>
      </Card>
    </main>
  );
}
