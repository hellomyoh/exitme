"use client";

/** 계정 관리 (관리자 전용) — 계정 발급·목록 (2026-09-01 지시). 발급 계정은 첫 로그인에서 비밀번호 변경 강제. */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ensureSession, fetchMe } from "../../../lib/api";
import { Callout, Card, CardTitle, PageTitle } from "../../../components/ui";

type Row = { id: number; login: string; is_admin: boolean; must_change_password: boolean; created_at: string | null };

export default function AccountsPage() {
  const router = useRouter();
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [loginId, setLoginId] = useState("");
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState("");

  async function load() {
    const res = await apiFetch("/auth/admin/users");
    if (res.ok) setRows(((await res.json()) as { items: Row[] }).items);
  }

  useEffect(() => {
    void ensureSession().then(async (ok) => {
      if (!ok) { router.push("/login"); return; }
      const me = await fetchMe();
      setAllowed(me?.is_admin === true);
      if (me?.is_admin) void load();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  async function create() {
    setMsg("");
    if (loginId.trim().length < 2 || pw.length < 6) { setMsg("아이디 2자·비밀번호 6자 이상이어야 합니다"); return; }
    const res = await apiFetch("/auth/admin/users", {
      method: "POST", body: JSON.stringify({ login: loginId.trim(), password: pw }),
    });
    if (res.ok) {
      setMsg(`✅ 발급됨 — ${loginId.trim()} / (지정한 임시 비밀번호). 첫 로그인 시 비밀번호 변경이 강제됩니다.`);
      setLoginId(""); setPw("");
      void load();
    } else setMsg(((await res.json()) as { detail?: string }).detail ?? `발급 실패 (${res.status})`);
  }

  if (allowed === false) {
    return <main><PageTitle title="계정 관리" sub="" /><Callout icon="⛔">관리자 전용 메뉴입니다.</Callout></main>;
  }
  return (
    <main>
      <PageTitle title="계정 관리" sub="계정을 직접 발급해 전달합니다 — 발급 계정은 첫 로그인에서 비밀번호를 변경해야 합니다" />
      <Card className="mb-4 max-w-2xl">
        <CardTitle>새 계정 발급</CardTitle>
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1 text-xs text-faint">아이디
            <input className="input w-44" value={loginId} onChange={(e) => setLoginId(e.target.value)} /></label>
          <label className="grid gap-1 text-xs text-faint">임시 비밀번호 (6자 이상)
            <input className="input w-44" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
          <button className="btn btn-primary" onClick={() => void create()}>발급</button>
        </div>
        {msg && <p className="mt-3 text-[13.5px] text-muted">{msg}</p>}
      </Card>
      <Card className="max-w-2xl">
        <CardTitle>계정 목록 <span className="normal-case text-faint">· {rows.length}명</span></CardTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-[14px]">
            <thead><tr className="border-b border-line text-left text-[12.5px] text-faint">
              <th className="pb-2 font-medium">아이디</th>
              <th className="pb-2 font-medium">권한</th>
              <th className="pb-2 font-medium">상태</th>
              <th className="pb-2 font-medium">생성일</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-line/50 last:border-0">
                  <td className="py-2 font-semibold">{r.login}</td>
                  <td className="py-2">{r.is_admin ? <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[11.5px] font-bold text-accent">관리자</span> : "일반"}</td>
                  <td className="py-2 text-[13px] text-muted">{r.must_change_password ? "첫 로그인 대기 (비밀번호 변경 필요)" : "활성"}</td>
                  <td className="py-2 text-[13px] text-faint">{r.created_at?.slice(0, 10) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </main>
  );
}
