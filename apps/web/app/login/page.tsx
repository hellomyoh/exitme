"use client";

/** 로그인/가입 — access는 메모리 보관, refresh는 httpOnly 쿠키 (ADR-003). */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login, register } from "../../lib/api";

const box = { background: "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "8px 10px" } as const;

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");

  async function submit(kind: "login" | "register") {
    const ok = kind === "login" ? await login(email, password) : await register(email, password);
    if (ok) router.push("/chart");
    else setMsg(kind === "login" ? "로그인 실패 — 이메일/비밀번호를 확인하세요" : "가입 실패 — 이미 등록된 이메일이거나 비밀번호가 8자 미만입니다");
  }

  return (
    <main style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, width: 300 }}>
        <h1 style={{ fontSize: "1.4rem", marginBottom: 8 }}>StockLab 로그인</h1>
        <input style={box} placeholder="이메일" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input style={box} placeholder="비밀번호 (8자 이상)" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <div style={{ display: "flex", gap: 8 }}>
          <button style={{ ...box, flex: 1, cursor: "pointer" }} onClick={() => void submit("login")}>로그인</button>
          <button style={{ ...box, flex: 1, cursor: "pointer" }} onClick={() => void submit("register")}>가입</button>
        </div>
        {msg && <p style={{ color: "#f2617a", fontSize: 13 }}>{msg}</p>}
      </div>
    </main>
  );
}
