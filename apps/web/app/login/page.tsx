"use client";

/** 로그인/가입 — access는 메모리, refresh는 httpOnly 쿠키 (ADR-003). */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "../../lib/api";
import { useFieldErrors } from "../../lib/form";
import { BrandMark, BrandWord } from "../../components/brand";
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
  const fe = useFieldErrors();   // 입력 누락 표시 (2026-09-10, lib/form)

  async function submit() {
    const bad = fe.validate({
      email: email.trim() ? "" : "아이디(이메일)를 넣으세요",
      password: password ? "" : "비밀번호를 넣으세요",
    });
    if (bad) { setMsg(`입력을 확인하세요 — ${bad}`); return; }
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
          <BrandMark className="h-8 w-8 text-ink" title="ExitMe" />
          <h1 className="text-lg font-extrabold tracking-tight"><BrandWord /></h1>
        </div>
        <div className="grid gap-3">
          <label className="grid gap-1.5 text-xs text-faint">아이디
            <input className={`input${fe.cls("email")}`} value={email}
              onChange={(e) => { setEmail(e.target.value); fe.clear("email"); }} />{fe.msg("email")}</label>
          <label className="grid gap-1.5 text-xs text-faint">비밀번호
            <input className={`input${fe.cls("password")}`} type="password" value={password}
              onKeyDown={(e) => e.key === "Enter" && void submit()}
              onChange={(e) => { setPassword(e.target.value); fe.clear("password"); }} />{fe.msg("password")}</label>
          <button className="btn btn-primary mt-1" disabled={busy} onClick={() => void submit()}>로그인</button>
          {msg && <p className={`text-[13px] ${fe.has || msg.includes("실패") || msg.includes("확인") ? "font-medium text-down" : "text-up"}`}>{msg}</p>}
        </div>
      </Card>
    </main>
  );
}
