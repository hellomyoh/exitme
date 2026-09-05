"use client";

/** 일반 설정 — 비밀번호 변경·세션·로그아웃 (2026-08-31 지시). */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch, fetchMe, logout as apiLogout } from "../../lib/api";
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

  // 증권사 계좌 — 여기서 등록하고 실전매매에서 선택해 쓴다 (2026-09-05 지시)
  type Acct = { id: number; label: string; env: string; acnt_prdt_cd: string; app_key: string;
    account_no: string; last_import_at: string | null; linked_portfolios: string[] };
  type ProbeAcct = { account_no: string; acnt_prdt_cd: string; label: string; holdings: number; deposit: number; total_eval: number };
  const [accts, setAccts] = useState<Acct[]>([]);
  const [af, setAf] = useState({ label: "", app_key: "", app_secret: "", account_no: "", acnt_prdt_cd: "01", env: "prod" });
  const [acctOpen, setAcctOpen] = useState(false);
  const [probe, setProbe] = useState<ProbeAcct[] | null>(null);
  const [acctMsg, setAcctMsg] = useState("");
  const loadAccts = useCallback(() => {
    void apiFetch("/broker/accounts").then(async (r) => {
      if (r.ok) setAccts(((await r.json()) as { items: Acct[] }).items);
    });
  }, []);
  useEffect(() => loadAccts(), [loadAccts]);

  // 챗봇 시스템 프롬프트 — 전역·관리자 전용 (2026-09-04). 빈 값 저장 = 기본 복귀.
  const [isAdmin, setIsAdmin] = useState(false);
  const [sysPrompt, setSysPrompt] = useState("");
  const [sysDefault, setSysDefault] = useState("");
  const [sysUsingDefault, setSysUsingDefault] = useState(true);
  const [sysMsg, setSysMsg] = useState("");
  useEffect(() => {
    void fetchMe().then((me) => {
      if (!me?.is_admin) return;
      setIsAdmin(true);
      void apiFetch("/settings/chat-system").then(async (r) => {
        if (!r.ok) return;
        const j = (await r.json()) as { prompt: string; default: string };
        setSysDefault(j.default);
        setSysUsingDefault(!j.prompt);
        setSysPrompt(j.prompt || j.default);
      });
    });
  }, []);
  async function saveSysPrompt(text: string) {
    const r = await apiFetch("/settings/chat-system", { method: "PUT", body: JSON.stringify({ prompt: text }) });
    if (!r.ok) { setSysMsg(`저장 실패 (${r.status})`); return; }
    const usingDefault = ((await r.json()) as { using_default: boolean }).using_default;
    setSysUsingDefault(usingDefault);
    if (usingDefault) setSysPrompt(sysDefault);
    setSysMsg(usingDefault ? "✅ 초기화 — 내장 기본 프롬프트 사용" : "✅ 저장 — 다음 대화부터 적용");
  }

  const forcePw = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("force_pw") === "1";
  return (
    <main className="mx-auto max-w-4xl">
      <PageTitle title="일반 설정" sub="계정·세션·챗봇 관리" />
      {forcePw && (
        <Callout icon="🔐">
          <b className="text-ink">첫 로그인입니다 — 비밀번호를 변경해야 다른 메뉴를 사용할 수 있습니다.</b>{" "}
          아래에서 현재(임시) 비밀번호와 새 비밀번호를 입력하세요.
        </Callout>
      )}
      <div className="mb-4" />
      <Card className="mb-4">
        <CardTitle>비밀번호 변경</CardTitle>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="grid gap-1 text-[13px] text-faint">현재 비밀번호
            <input type="password" className="input w-full min-w-0" value={cur} onChange={(e) => setCur(e.target.value)} /></label>
          <label className="grid gap-1 text-[13px] text-faint">새 비밀번호 (8자 이상)
            <input type="password" className="input w-full min-w-0" value={pw1} onChange={(e) => setPw1(e.target.value)} /></label>
          <label className="grid gap-1 text-[13px] text-faint">새 비밀번호 확인
            <input type="password" className="input w-full min-w-0" value={pw2} onChange={(e) => setPw2(e.target.value)} /></label>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button className="btn btn-primary" onClick={() => void changePw()}>변경</button>
          {msg && <span className="text-[13.5px] text-muted">{msg}</span>}
        </div>
      </Card>
      <Card className="mb-4">
        <CardTitle>세션</CardTitle>
        <p className="mb-3 text-[14px] leading-relaxed text-muted">
          로그인 세션은 <b className="text-ink">마지막 활동 후 3시간</b> 유지됩니다(활동 시 자동 연장).
          만료되면 로그인 화면으로 안내됩니다.
        </p>
        <button className="btn" onClick={logout}>로그아웃</button>
      </Card>
      <Card className="mb-4">
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
      <Card className="mb-4">
        <CardTitle right={
          <button className="text-[12.5px] font-normal normal-case text-accent"
            onClick={() => { setAcctOpen(!acctOpen); setProbe(null); setAcctMsg(""); }}>
            {acctOpen ? "닫기" : "＋ 계좌 등록"}</button>
        }>증권사 계좌 <span className="normal-case text-faint">
          · 여기서 등록하고 실전매매 화면에서 선택해 사용합니다 (조회 전용 — 주문은 하지 않습니다)</span>
        </CardTitle>

        {accts.length === 0 && !acctOpen && (
          <p className="text-[13.5px] text-muted">
            등록된 계좌가 없습니다. 한국투자증권 앱키·시크릿·계좌번호를 등록하면 체결 내역을 자동으로 불러올 수 있습니다.
          </p>
        )}
        {accts.length > 0 && (
          <div className="grid gap-2">
            {accts.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-line bg-inset px-3 py-2 text-[13.5px]">
                <span className="font-semibold">{a.label}</span>
                <span className="text-muted">{a.account_no}-{a.acnt_prdt_cd}</span>
                <span className={a.env === "vps" ? "text-warn" : "text-muted"}>{a.env === "vps" ? "모의투자" : "실전"}</span>
                <span className="text-faint">
                  {a.linked_portfolios.length > 0 ? `연결: ${a.linked_portfolios.join(", ")}` : "연결된 포트 없음"}
                </span>
                <button className="ml-auto text-[12.5px] text-faint transition-colors hover:text-down"
                  onClick={() => void (async () => {
                    if (!window.confirm(`'${a.label}' 계좌 등록을 삭제할까요? 연결된 포트의 연동이 해제됩니다.`)) return;
                    const r = await apiFetch(`/broker/accounts/${a.id}`, { method: "DELETE" });
                    if (r.ok) loadAccts();
                  })()}>삭제</button>
              </div>
            ))}
          </div>
        )}

        {acctOpen && (
          <div className="mt-3 grid gap-3 border-t border-line pt-3 sm:grid-cols-2">
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">별칭 (선택)
              <input className="input w-full min-w-0" placeholder="예: 한투 메인" value={af.label}
                onChange={(e) => setAf({ ...af, label: e.target.value })} /></label>
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">계좌번호 (앞 8자리)
              <input className="input w-full min-w-0" placeholder="12345678" value={af.account_no}
                onChange={(e) => setAf({ ...af, account_no: e.target.value })} /></label>
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">앱키(App Key)
              <input className="input w-full min-w-0" value={af.app_key}
                onChange={(e) => setAf({ ...af, app_key: e.target.value })} /></label>
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">앱시크릿(App Secret)
              <input type="password" className="input w-full min-w-0" value={af.app_secret}
                onChange={(e) => setAf({ ...af, app_secret: e.target.value })} /></label>
            <div className="grid grid-cols-2 gap-3">
              <label className="grid min-w-0 gap-1 text-[13px] text-faint">환경
                <select className="input w-full min-w-0" value={af.env}
                  onChange={(e) => setAf({ ...af, env: e.target.value })}>
                  <option value="prod">실전</option><option value="vps">모의투자</option>
                </select></label>
              <div className="grid content-end">
                {/* KIS 는 계좌 목록 API 가 없어, 입력 계좌를 실제 조회해 상품코드까지 확인한다 */}
                <button className="btn !py-2" disabled={!(af.app_key && af.app_secret && af.account_no)}
                  onClick={() => void (async () => {
                    setAcctMsg("조회 중…"); setProbe(null);
                    const r = await apiFetch("/broker/probe", { method: "POST", body: JSON.stringify({
                      app_key: af.app_key, app_secret: af.app_secret, account_no: af.account_no, env: af.env }) });
                    if (r.ok) { setProbe(((await r.json()) as { accounts: ProbeAcct[] }).accounts); setAcctMsg(""); }
                    else setAcctMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `조회 실패 (${r.status})`);
                  })()}>계좌 조회</button>
              </div>
            </div>
            {(probe || acctMsg) && (
              <div className="sm:col-span-2">
                {acctMsg && <p className="text-[13px] text-up">{acctMsg}</p>}
                {probe && probe.length > 0 && (
                  <>
                    <div className="mb-1 text-[12.5px] text-faint">확인된 계좌 — 선택하면 그 계좌로 등록됩니다</div>
                    <div className="grid gap-1.5">
                      {probe.map((a) => (
                        <button key={a.label}
                          onClick={() => setAf({ ...af, account_no: a.account_no, acnt_prdt_cd: a.acnt_prdt_cd })}
                          className={`flex flex-wrap items-center gap-x-4 rounded-lg border px-3 py-2 text-left text-[13.5px] transition-colors ${
                            af.acnt_prdt_cd === a.acnt_prdt_cd && af.account_no === a.account_no
                              ? "border-accent bg-accent-dim font-semibold" : "border-line bg-inset hover:border-line-strong"}`}>
                          <span className="font-semibold">{a.label}</span>
                          <span className="text-muted">보유 {a.holdings}종목</span>
                          <span className="text-muted">예수금 {a.deposit.toLocaleString()}원</span>
                          <span className="text-muted">평가 {a.total_eval.toLocaleString()}원</span>
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
            <div className="flex items-center gap-3 sm:col-span-2">
              <button className="btn btn-primary"
                disabled={!(af.app_key && af.app_secret && af.account_no)}
                onClick={() => void (async () => {
                  const r = await apiFetch("/broker/accounts", { method: "POST", body: JSON.stringify(af) });
                  if (r.ok) { setAcctOpen(false); setProbe(null); setAcctMsg("");
                    setAf({ label: "", app_key: "", app_secret: "", account_no: "", acnt_prdt_cd: "01", env: "prod" });
                    loadAccts(); }
                  else setAcctMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `등록 실패 (${r.status})`);
                })()}>등록</button>
              <button className="btn" onClick={() => setAcctOpen(false)}>취소</button>
              <span className="text-[12px] text-faint">키는 서버에 암호화 저장되며 화면에는 마스킹만 표시됩니다.</span>
            </div>
          </div>
        )}
      </Card>

      {isAdmin && (
        <Card className="mb-4">
          <CardTitle right={<span className="text-[12px] font-normal normal-case text-faint">
            {sysUsingDefault ? "내장 기본 사용 중" : "⚠️ 교체본 사용 중 — 이후 전략 개정이 자동 반영되지 않음"}</span>}>
            챗봇 시스템 프롬프트 (관리자)
          </CardTitle>
          <p className="mb-2 text-[13.5px] leading-relaxed text-muted">
            매매 도우미의 본문(역할·전략 지식·답변 스타일)을 <b className="text-ink">전체 교체</b>합니다 — 모든 사용자 공통.
            도구 사용 규칙(수치는 조회 후 답변·읽기 전용·단위 환산)은 <b className="text-ink">시스템 계약으로 항상 첨부</b>되어 교체할 수 없습니다.
          </p>
          <textarea rows={14} maxLength={8000} className="input w-full resize-y font-mono text-[12.5px] leading-relaxed"
            value={sysPrompt} onChange={(e) => setSysPrompt(e.target.value)} />
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <button className="btn btn-primary" onClick={() => void saveSysPrompt(sysPrompt)}>저장</button>
            <button className="btn" title="편집기를 내장 기본 프롬프트로 채웁니다 (저장 전까지 반영 안 됨)"
              onClick={() => { setSysPrompt(sysDefault); setSysMsg("기본값을 불러왔습니다 — 저장해야 반영됩니다"); }}>기본값 불러오기</button>
            <button className="btn !text-up" title="교체본을 삭제하고 내장 기본 프롬프트로 되돌립니다"
              onClick={() => { if (window.confirm("시스템 프롬프트를 초기화할까요? 교체본이 삭제되고 내장 기본으로 돌아갑니다.")) void saveSysPrompt(""); }}>초기화</button>
            <span className="text-[12.5px] text-faint">{sysPrompt.length}/8000</span>
            {sysMsg && <span className="text-[13.5px] text-muted">{sysMsg}</span>}
          </div>
        </Card>
      )}
      <Callout icon="ℹ️">
        아이디(이메일) 변경은 지원하지 않습니다 — 새 계정을 만들어 사용하세요.
        알고리즘 상수는 <Link href="/settings/algorithm" className="font-semibold text-accent">알고리즘 설정</Link>에서 변경합니다.
      </Callout>
    </main>
  );
}
