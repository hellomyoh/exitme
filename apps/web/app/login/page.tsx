"use client";

/** 로그인/가입 — access는 메모리, refresh는 httpOnly 쿠키 (ADR-003). */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login, register } from "../../lib/api";
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

  async function submit(kind: "login" | "register") {
    setBusy(true);
    setMsg("");
    const ok = kind === "login" ? await login(email, password) : await register(email, password);
    setBusy(false);
    if (ok) router.push("/dashboard");
    else setMsg(kind === "login"
      ? "로그인 실패 — 이메일/비밀번호를 확인하세요"
      : "가입 실패 — 이미 등록된 이메일이거나 비밀번호가 8자 미만입니다");
  }

  return (
    <main className="flex w-full items-center justify-center">
      <Card className="w-full max-w-sm !p-8">
        <div className="mb-6 flex items-center gap-2">
          <span className="inline-block h-3 w-3 rounded-sm bg-accent" />
          <h1 className="text-lg font-extrabold tracking-tight">StockLab</h1>
        </div>
        <div className="grid gap-3">
          <label className="grid gap-1.5 text-xs text-faint">이메일
            <input className="input" placeholder="you@example.com" value={email}
              onChange={(e) => setEmail(e.target.value)} /></label>
          <label className="grid gap-1.5 text-xs text-faint">비밀번호 (8자 이상)
            <input className="input" type="password" value={password}
              onKeyDown={(e) => e.key === "Enter" && void submit("login")}
              onChange={(e) => setPassword(e.target.value)} /></label>
          <button className="btn btn-primary mt-1" disabled={busy} onClick={() => void submit("login")}>로그인</button>
          <button className="btn" disabled={busy} onClick={() => void submit("register")}>새 계정 가입</button>
          {msg && <p className="text-[13px] text-up">{msg}</p>}
        </div>
        <p className="mt-5 text-[11px] leading-relaxed text-faint">
          모의·과거 데이터 기반 서비스이며 투자 권유가 아닙니다.
        </p>
      </Card>
    </main>
  );
}
