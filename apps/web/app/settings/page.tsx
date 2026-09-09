"use client";

/** 일반 설정 — 비밀번호 변경·세션·로그아웃 (2026-08-31 지시). */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch, fetchMe, logout as apiLogout, type Me } from "../../lib/api";
import { Callout, Card, CardTitle, PageTitle } from "../../components/ui";

/** 증권사 자격 입력 (2026-09-05 지시) — 저장값을 중간 마스킹으로 필드 안에 보여주고 브라우저 자동완성을 막는다.
 *
 *  type="password" 를 쓰자 크롬이 이 사이트의 로그인 아이디/비밀번호를 앱키/시크릿에 채워 넣었고(스샷: myoh / ●●●●●●●●●),
 *  그 9자 비밀번호가 시크릿으로 저장돼 계좌 조회가 실패했다. 비밀번호 필드일 이유가 없어(저장값은 어차피 마스킹)
 *  일반 text 로 두고, 입력 중인 값도 가리지 않는다 — 붙여넣은 값을 눈으로 확인할 수 있어야 이런 오입력을 바로 잡는다.
 *  비어 있고 포커스가 없을 때는 저장값(마스킹)을 회색으로 보여주고, 포커스하면 새 값을 입력받는다. */
function CredentialInput({ name, value, onChange, stored, placeholder = "" }: {
  name: string; value: string; onChange: (v: string) => void; stored?: string; placeholder?: string;
}) {
  const [focus, setFocus] = useState(false);
  const showStored = !focus && value === "" && !!stored;
  return (
    <input type="text" name={name} autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false}
      data-1p-ignore="true" data-lpignore="true" data-bwignore="true" data-form-type="other"
      className={`input w-full min-w-0 ${showStored ? "text-faint" : ""}`}
      value={showStored ? stored : value}
      placeholder={showStored ? "" : placeholder}
      onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}
      onChange={(e) => onChange(e.target.value)} />
  );
}

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
    app_secret: string; account_no: string; last_import_at: string | null; linked_portfolios: string[] };
  type ProbeAcct = { account_no: string; acnt_prdt_cd: string; label: string; holdings: number; deposit: number; total_eval: number };
  const [accts, setAccts] = useState<Acct[]>([]);
  const [af, setAf] = useState({ label: "", app_key: "", app_secret: "", account_no: "", acnt_prdt_cd: "01", env: "prod" });
  const [acctOpen, setAcctOpen] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);   // null = 신규 등록
  const [testRes, setTestRes] = useState<Record<number, { ok: boolean; message?: string; holdings?: number; deposit?: number;
    suggest?: { acnt_prdt_cd: string; holdings: number; deposit: number } | null }>>({});
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
  const [me, setMe] = useState<Me | null>(null);   // 자산 초기화 확인용 로그인 아이디
  // 자산 전체 초기화 (2026-09-05 지시) — 되돌릴 수 없어 아이디 입력 확인
  const [rsScopes, setRsScopes] = useState<Record<string, boolean>>({ portfolios: true, journals: true, manual_assets: true, snapshots: true });
  const [rsConfirm, setRsConfirm] = useState("");
  const [rsMsg, setRsMsg] = useState("");
  const [rsBusy, setRsBusy] = useState(false);
  async function resetAssets() {
    if (!me) return;
    if (!window.confirm("정말 초기화할까요? 선택한 항목의 데이터가 모두 삭제되며 되돌릴 수 없습니다.")) return;
    setRsBusy(true); setRsMsg("");
    const r = await apiFetch("/account/reset-assets", { method: "POST", body: JSON.stringify({
      confirm: rsConfirm.trim(), scopes: Object.entries(rsScopes).filter(([, v]) => v).map(([k]) => k) }) });
    const j = (await r.json().catch(() => ({}))) as { deleted?: Record<string, number>; detail?: string };
    setRsBusy(false);
    if (!r.ok) { setRsMsg(j.detail ?? `실패 (${r.status})`); return; }
    const d = j.deleted ?? {};
    setRsMsg(`초기화 완료 — 실전매매 ${d.portfolios ?? 0}개 · 매매일지 ${d.journals ?? 0}개 · 기타 자산 ${d.manual_assets ?? 0}건 · 자산 추이 ${d.snapshots ?? 0}일`);
    setRsConfirm("");
  }
  const [sysPrompt, setSysPrompt] = useState("");
  const [sysDefault, setSysDefault] = useState("");
  const [sysUsingDefault, setSysUsingDefault] = useState(true);
  const [sysMsg, setSysMsg] = useState("");
  useEffect(() => {
    void fetchMe().then((me) => {
      setMe(me);
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

  const curAcct = editId === null ? null : accts.find((x) => x.id === editId) ?? null;
  const forcePw = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("force_pw") === "1";
  // 섹션 탭 (2026-09-05 지시) — 카드 세로 나열 대신 탭으로 분리, ?tab= 로 상태 공유
  const TABS_ALL = [
    { key: "account", label: "계정", desc: "비밀번호 · 세션" },
    { key: "broker", label: "증권사 계좌", desc: "체결 자동 가져오기 연동" },
    { key: "auto", label: "무인 실행", desc: "계좌별 무인 매수·매도 플래그 · 하루 매수 상한" },
    { key: "notify", label: "알림", desc: "텔레그램 봇 · 보낼 항목" },
    { key: "chat", label: "챗봇", desc: "시스템 프롬프트 (관리자)", adminOnly: true },
  ] as const;
  type TabKey = (typeof TABS_ALL)[number]["key"];
  // 챗봇 탭은 관리자 전용 — 일반 계정에는 추가 지침 카드도 보이지 않는다 (2026-09-05 지시)
  const TABS = TABS_ALL.filter((t) => !("adminOnly" in t && t.adminOnly) || isAdmin);
  const [tab, setTab] = useState<TabKey>("account");
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("tab") as TabKey | null;
    if (q && TABS_ALL.some((t) => t.key === q)) setTab(q);
    if (new URLSearchParams(window.location.search).get("force_pw") === "1") setTab("account");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  function goTab(k: TabKey) {
    setTab(k);
    const u = new URL(window.location.href);
    u.searchParams.set("tab", k);
    window.history.replaceState(null, "", u.toString());
  }

  return (
    <main>
      <PageTitle title="일반 설정" sub="계정 · 증권사 연동 · 챗봇" />
      {forcePw && (
        <Callout icon="🔐">
          <b className="text-ink">첫 로그인입니다 — 비밀번호를 변경해야 다른 메뉴를 사용할 수 있습니다.</b>{" "}
          아래에서 현재(임시) 비밀번호와 새 비밀번호를 입력하세요.
        </Callout>
      )}
      {/* 탭 바 — 알약형, 활성은 흰 배경(사이드바·포트 탭과 동일 언어) */}
      <div className="mb-5 flex flex-wrap gap-1.5 border-b border-line pb-3">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => goTab(t.key)}
            className={`rounded-lg border px-4 py-2 text-left transition-colors ${
              tab === t.key ? "border-line bg-surface shadow-sm" : "border-transparent hover:bg-surface/60"}`}>
            <span className={`block text-[14px] ${tab === t.key ? "font-semibold text-ink" : "text-muted"}`}>{t.label}</span>
            <span className="block text-[11.5px] text-faint">{t.desc}</span>
          </button>
        ))}
      </div>

      {tab === "auto" && <AutoExecSettings />}
      {tab === "notify" && <NotifySettings />}

      {tab === "account" && (<>
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
      {/* 자산 전체 초기화 (2026-09-05 지시) — 계정·증권사 계좌 자격·설정은 유지, 자산 데이터만 삭제 */}
      <Card className="mb-4 border-down/30">
        <CardTitle>자산 전체 초기화 <span className="normal-case text-faint">· 되돌릴 수 없습니다</span></CardTitle>
        <p className="mb-3 text-[13.5px] leading-relaxed text-muted">
          이 계정의 자산 데이터를 처음 상태로 되돌립니다. 계정·비밀번호·증권사 계좌 등록·설정은 남습니다.
          증권사에 접수된 예약주문은 취소되지 않으니 필요하면 HTS 에서 먼저 정리하세요.
        </p>
        <div className="mb-3 grid gap-1.5 text-[13.5px] sm:grid-cols-2">
          {([["portfolios", "실전매매 포트 전체 — 거래·보유·주문표·예약주문 기록"], ["journals", "매매일지 전체 — 기록 포함"],
             ["manual_assets", "기타 자산(수동 입력)"], ["snapshots", "자산 추이(일별 스냅샷)"]] as const).map(([k, label]) => (
            <label key={k} className="inline-flex items-center gap-2">
              <input type="checkbox" className="h-4 w-4" checked={rsScopes[k]} onChange={(e) => setRsScopes({ ...rsScopes, [k]: e.target.checked })} />
              {label}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="grid gap-1 text-[13px] text-faint">확인을 위해 로그인 아이디 입력{me ? ` (${me.login})` : ""}
            <input className="input w-64 min-w-0" autoComplete="off" value={rsConfirm} onChange={(e) => setRsConfirm(e.target.value)} placeholder={me?.login ?? ""} /></label>
          <button className="btn !border-down !text-down hover:!bg-down hover:!text-white"
            disabled={rsBusy || !me || rsConfirm.trim() !== me.login || !Object.values(rsScopes).some(Boolean)}
            onClick={() => void resetAssets()}>{rsBusy ? "초기화 중…" : "자산 초기화"}</button>
          {rsMsg && <span className="text-[13.5px] text-muted">{rsMsg}</span>}
        </div>
      </Card>
      </>)}

      {tab === "chat" && isAdmin && (<>
      <Card className="mb-4">
        <CardTitle>챗봇 추가 지침 <span className="normal-case text-faint">· 관리자 계정 전용</span></CardTitle>
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
      </>)}

      {tab === "broker" && (
      <Card className="mb-4">
        <CardTitle right={
          <button className="text-[12.5px] font-normal normal-case text-accent"
            onClick={() => {
              setAcctOpen(!acctOpen); setProbe(null); setAcctMsg(""); setEditId(null);
              setAf({ label: "", app_key: "", app_secret: "", account_no: "", acnt_prdt_cd: "01", env: "prod" });
            }}>
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
                <span className="text-faint">키 {a.app_key}</span>
                <span className={a.env === "vps" ? "text-warn" : "text-muted"}>{a.env === "vps" ? "모의투자" : "실전"}</span>
                <span className="text-faint">
                  {a.linked_portfolios.length > 0 ? `연결: ${a.linked_portfolios.join(", ")}` : "연결된 포트 없음"}
                </span>
                {testRes[a.id] && (
                  <span className={`text-[12.5px] ${testRes[a.id].ok ? "text-ok" : "text-up"}`}>
                    {testRes[a.id].ok
                      ? `✓ 정상 (보유 ${testRes[a.id].holdings}종목 · 예수금 ${(testRes[a.id].deposit ?? 0).toLocaleString()}원)`
                      : `✗ ${testRes[a.id].message}`}
                    {/* 상품코드만 틀린 경우 — 되는 코드를 찾아 한 번에 고칠 수 있게 (2026-09-05) */}
                    {!testRes[a.id].ok && testRes[a.id].suggest && (
                      <button className="ml-2 rounded border border-accent px-1.5 py-0.5 text-[11.5px] font-semibold text-accent"
                        onClick={() => void (async () => {
                          const cd = testRes[a.id].suggest!.acnt_prdt_cd;
                          const r = await apiFetch(`/broker/accounts/${a.id}`, { method: "PUT", body: JSON.stringify({ acnt_prdt_cd: cd }) });
                          if (r.ok) { setTestRes((p2) => ({ ...p2, [a.id]: { ok: true, holdings: testRes[a.id].suggest!.holdings, deposit: testRes[a.id].suggest!.deposit } })); loadAccts(); }
                        })()}>
                        상품코드 {testRes[a.id].suggest!.acnt_prdt_cd} 로 고치기 (보유 {testRes[a.id].suggest!.holdings}종목)
                      </button>
                    )}
                  </span>
                )}
                <button className="ml-auto text-[12.5px] text-muted transition-colors hover:text-ink"
                  onClick={() => void (async () => {
                    setTestRes({ ...testRes, [a.id]: { ok: false, message: "확인 중…" } });
                    const r = await apiFetch(`/broker/accounts/${a.id}/test`, { method: "POST" });
                    const j = r.ok ? await r.json() : { ok: false, message: `요청 실패 (${r.status})` };
                    setTestRes((prev) => ({ ...prev, [a.id]: j }));
                  })()}>연결 확인</button>
                <button className="text-[12.5px] text-accent transition-colors hover:underline"
                  onClick={() => {
                    setEditId(a.id); setAcctOpen(true); setProbe(null); setAcctMsg("");
                    // 키는 비워 둔다 — 저장 시 비어 있으면 기존 키 유지
                    setAf({ label: a.label, app_key: "", app_secret: "",
                            account_no: "", acnt_prdt_cd: a.acnt_prdt_cd, env: a.env });
                  }}>수정</button>
                <button className="text-[12.5px] text-faint transition-colors hover:text-down"
                  onClick={() => void (async () => {
                    if (!window.confirm(`'${a.label}' 계좌 등록을 삭제할까요? 연결된 포트의 연동이 해제됩니다.`)) return;
                    const r = await apiFetch(`/broker/accounts/${a.id}`, { method: "DELETE" });
                    if (r.ok) { loadAccts(); if (editId === a.id) { setAcctOpen(false); setEditId(null); } }
                  })()}>삭제</button>
              </div>
            ))}
          </div>
        )}

        {acctOpen && (
          <div className="mt-3 grid gap-3 border-t border-line pt-3 sm:grid-cols-2 lg:grid-cols-3">
            <p className="text-[13px] text-muted sm:col-span-2 lg:col-span-3">
              {editId === null ? "새 계좌를 등록합니다."
                : <>계좌를 수정합니다 — 아래 회색 글씨가 <b className="text-ink">현재 저장된 값(중간 자리 가림)</b>입니다.
                   비워두면 그대로 유지되고, 새로 입력한 항목만 교체됩니다.</>}
            </p>
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">별칭 (선택)
              <input className="input w-full min-w-0" placeholder="예: 한투 메인" value={af.label}
                onChange={(e) => setAf({ ...af, label: e.target.value })} /></label>
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">계좌번호 (앞 8자리)
              <CredentialInput name="kis-account-no" value={af.account_no}
                onChange={(v) => setAf({ ...af, account_no: v })}
                stored={curAcct ? `${curAcct.account_no}-${curAcct.acnt_prdt_cd}` : undefined}
                placeholder={editId === null ? "12345678" : "새 계좌번호 (비우면 유지)"} /></label>
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">앱키(App Key)
              <CredentialInput name="kis-app-key" value={af.app_key}
                onChange={(v) => setAf({ ...af, app_key: v })} stored={curAcct?.app_key}
                placeholder={editId === null ? "KIS Developers 앱키 36자" : "새 앱키 (비우면 유지)"} /></label>
            <label className="grid min-w-0 gap-1 text-[13px] text-faint">앱시크릿(App Secret)
              <CredentialInput name="kis-app-secret" value={af.app_secret}
                onChange={(v) => setAf({ ...af, app_secret: v })} stored={curAcct?.app_secret}
                placeholder={editId === null ? "KIS Developers 앱시크릿 180자" : "새 시크릿 (비우면 유지)"} /></label>
            <div className="grid grid-cols-2 gap-3">
              <label className="grid min-w-0 gap-1 text-[13px] text-faint">환경
                <select className="input w-full min-w-0" value={af.env}
                  onChange={(e) => setAf({ ...af, env: e.target.value })}>
                  <option value="prod">실전</option><option value="vps">모의투자</option>
                </select></label>
              <div className="grid content-end">
                {/* KIS 는 계좌 목록 API 가 없어, 입력 계좌를 실제 조회해 상품코드까지 확인한다 */}
                <button className="btn !py-2" title={editId !== null ? "조회하려면 앱키·시크릿·계좌번호를 입력하세요" : ""}
                  disabled={!(af.app_key && af.app_secret && af.account_no)}
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
              <div className="sm:col-span-2 lg:col-span-3">
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
            <div className="flex items-center gap-3 sm:col-span-2 lg:col-span-3">
              <button className="btn btn-primary"
                disabled={editId === null && !(af.app_key && af.app_secret && af.account_no)}
                onClick={() => void (async () => {
                  const body = editId === null ? af : {
                    label: af.label, env: af.env, acnt_prdt_cd: af.acnt_prdt_cd,
                    ...(af.account_no.trim() ? { account_no: af.account_no } : {}),
                    ...(af.app_key.trim() ? { app_key: af.app_key } : {}),
                    ...(af.app_secret.trim() ? { app_secret: af.app_secret } : {}),
                  };
                  const r = await apiFetch(editId === null ? "/broker/accounts" : `/broker/accounts/${editId}`,
                    { method: editId === null ? "POST" : "PUT", body: JSON.stringify(body) });
                  if (r.ok) { setAcctOpen(false); setProbe(null); setAcctMsg(""); setEditId(null);
                    setAf({ label: "", app_key: "", app_secret: "", account_no: "", acnt_prdt_cd: "01", env: "prod" });
                    loadAccts(); }
                  else if (r.status === 404) {
                    // 다른 곳에서 삭제된 계좌를 수정하려 한 경우 — 목록을 새로 고치고 폼을 닫는다 (2026-09-05)
                    setAcctMsg("이미 삭제된 계좌입니다 — 목록을 새로 고쳤습니다.");
                    setEditId(null); setAcctOpen(false); loadAccts();
                  }
                  else setAcctMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `저장 실패 (${r.status})`);
                })()}>{editId === null ? "등록" : "저장"}</button>
              <button className="btn" onClick={() => { setAcctOpen(false); setEditId(null); }}>취소</button>
              <span className="text-[12px] text-faint">키는 서버에 암호화 저장되며 화면에는 마스킹만 표시됩니다.</span>
            </div>
          </div>
        )}
      </Card>

      )}

      {tab === "chat" && isAdmin && (
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
      {tab === "account" && (
      <Callout icon="ℹ️">
        아이디(이메일) 변경은 지원하지 않습니다 — 새 계정을 만들어 사용하세요.
        {isAdmin && <> 알고리즘 상수는 <Link href="/settings/algorithm" className="font-semibold text-accent">알고리즘 설정</Link>에서 변경합니다.</>}
      </Callout>
      )}
    </main>
  );
}

/** 무인 매매 플래그 — **증권사 계좌별** (2026-09-07 지시, ADR-009 2026-09-08). 포트에 연결된 계좌의 플래그가 09:01 실행의 유일한 판정 기준이다.
 *  승인 단계·주문표 버튼은 없다. 끄면 그 방향의 오늘 무인 주문이 즉시 취소된다(사용자 결정). 옵션 설명은 상단에 한 번만, 계좌는 한 줄씩. */
type AE = { buy: boolean; sell: boolean; daily_buy_cap_pct: number };
type AEAccount = { id: number; label: string; account_no: string; acnt_prdt_cd: string; env: string; linked_portfolios: string[]; auto_exec: AE };
type AEView = { default: AE; accounts: AEAccount[]; cancelled?: number; failed?: number };
const AE_ROWS: { k: "buy" | "sell"; label: string; short: string; desc: string }[] = [
  { k: "buy", label: "무인 매수", short: "무인 매수", desc: "그리드 매수(지정가)·레버리지 진입(시장가). 켜면 이 계좌에 연결된 국내 포트가 매 실행일 09:01 에 그 순간의 원장으로 주문표를 계산해 곧바로 발주합니다 — 승인 단계 없음. 시가가 갭 취소 기준 이하면 그리드 매수는 내지 않고, 하루 매수 상한·매수가능조회에 맞춰 수량을 줄여 냅니다." },
  { k: "sell", label: "무인 매도", short: "무인 매도", desc: "익절·축소(지정가)·레버리지 청산(시장가). 꺼져 있으면 매도 줄은 '수동 처리'로 남고 매수만 발주됩니다. 계좌 보유를 넘는 매도는 내지 않으며, 매도는 매수보다 먼저 냅니다." },
];
const AE_ON_CONFIRM = "무인을 켭니다.\n\n· 이 계좌에 연결된 국내 포트는 매 실행일 09:01 에 그 순간의 원장·설정으로 주문표를 계산해 곧바로 발주합니다 — 주문표에서 따로 승인하지 않습니다.\n· 09:00 까지 등록한 입출금·체결은 그날 수량에 바로 반영됩니다. 09:01 에 시가 확인 → 갭 판정 → 원장 vs 계좌 잔고 대조 → 하루 매수 상한 → 매수가능조회 → 지정가·시장가 발주.\n· 발주 2회 연속 실패·잔고 대조 불일치·장 마감 대조의 계획 외 거래가 있으면 그 포트는 자동 정지됩니다(주문표 배너에서 다시 켜기).\n· 주문표의 '이번 실행일 무인 취소' 버튼으로 하루만 수동으로 돌릴 수 있습니다.\n\n계속할까요?";
const AE_OFF_CONFIRM = "무인을 끕니다.\n\n· 오늘 이미 증권사에 접수된 이 방향의 무인 주문(미체결)은 즉시 취소됩니다. 체결된 주문은 그대로입니다.\n· 다음 09:01 부터 이 방향은 발주하지 않습니다(주문표는 참고용 — HTS 에서 직접 주문).\n\n계속할까요?";

function AutoExecSettings() {
  const [v, setV] = useState<AEView | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [capDraft, setCapDraft] = useState<Record<number, string>>({});
  const load = useCallback(async () => {
    const r = await apiFetch("/settings/auto-exec");
    if (r.ok) setV((await r.json()) as AEView);
  }, []);
  useEffect(() => { void load(); }, [load]);
  const done = (j: AEView, who: string) => {
    setV(j);
    setMsg(`${who} 저장되었습니다${(j.cancelled ?? 0) > 0 ? ` · 살아 있는 무인 주문 ${j.cancelled}건 취소` : ""}${(j.failed ?? 0) > 0 ? ` · 취소 실패 ${j.failed}건 — 매매 로그를 확인하세요` : ""}`);
  };
  async function saveAccount(a: AEAccount, body: Partial<AE>, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true); setMsg("");
    const r = await apiFetch(`/settings/auto-exec/accounts/${a.id}`, { method: "PUT", body: JSON.stringify(body) });
    setBusy(false);
    if (r.ok) done((await r.json()) as AEView, a.label);
    else setMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `저장 실패 (${r.status})`);
  }
  async function saveAll(k: "buy" | "sell", val: boolean) {
    if (!v) return;
    if (!window.confirm(val ? AE_ON_CONFIRM : AE_OFF_CONFIRM)) return;
    if (!window.confirm(`모든 계좌(${v.accounts.length}개)에 적용하고 새 계좌의 기본값으로도 저장합니다. 계속할까요?`)) return;
    setBusy(true); setMsg("");
    const r = await apiFetch("/settings/auto-exec", { method: "PUT", body: JSON.stringify({ buy: v.default.buy, sell: v.default.sell, [k]: val }) });
    setBusy(false);
    if (r.ok) done((await r.json()) as AEView, "모든 계좌에");
    else setMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `저장 실패 (${r.status})`);
  }
  function commitCap(a: AEAccount) {
    const raw = (capDraft[a.id] ?? String(a.auto_exec.daily_buy_cap_pct)).trim().replace(",", ".");
    const n = raw === "" ? 0 : Number(raw);
    if (!Number.isFinite(n) || n < 0 || n > 100) { setMsg("하루 매수 상한은 0~100 사이의 %로 입력하세요 (0 = 없음)"); return; }
    if (n === a.auto_exec.daily_buy_cap_pct) return;
    void saveAccount(a, { daily_buy_cap_pct: n });
  }
  return (
    <Card className="mb-4">
      <CardTitle>무인 매수 · 매도 — 계좌별 플래그 <span className="normal-case text-faint">· 포트에 연결된 계좌의 플래그만 보고 09:01 에 계산·발주합니다 (ADR-009)</span></CardTitle>
      {/* 옵션 설명 — 한 번만 */}
      <div className="mb-4 grid gap-2 text-[13px] sm:grid-cols-3">
        {AE_ROWS.map((row) => (
          <div key={row.k} className="rounded-lg border border-line bg-inset px-3 py-2">
            <div className="font-semibold text-ink">{row.label} <span className="text-[11.5px] font-normal text-faint">기본 꺼짐</span></div>
            <div className="mt-0.5 leading-relaxed text-muted">{row.desc}</div>
          </div>
        ))}
        <div className="rounded-lg border border-line bg-inset px-3 py-2">
          <div className="font-semibold text-ink">하루 매수 상한 <span className="text-[11.5px] font-normal text-faint">총자산 대비 %, 기본 0 = 없음 (참고용)</span></div>
          <div className="mt-0.5 leading-relaxed text-muted">진입 속도 제한 — 그날 매수 합계가 총자산의 이 비율을 넘지 않게 수량을 줄여 냅니다(정지하지 않음). 안전장치가 아니라 취향값: 20%면 목표 도달 약 한 달, 0이면 약 3주.</div>
        </div>
      </div>
      {v && v.accounts.length === 0 && (
        <p className="text-[13.5px] text-muted">등록된 증권사 계좌가 없습니다 — <b className="text-ink">증권사 계좌</b> 탭에서 먼저 등록하면 여기에 계좌별 플래그가 나타납니다.</p>
      )}
      {v && v.accounts.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full whitespace-nowrap text-[13.5px]">
            <thead>
              <tr className="border-b border-line text-left text-[12px] text-faint">
                <th className="pb-2 pr-3 font-medium">계좌</th>
                <th className="pb-2 pr-3 font-medium">연결 포트</th>
                {AE_ROWS.map((row) => <th key={row.k} className="pb-2 px-3 text-center font-medium">{row.short}</th>)}
                <th className="pb-2 px-3 text-center font-medium">하루 매수 상한</th>
              </tr>
            </thead>
            <tbody>
              {v.accounts.map((a) => (
                <tr key={a.id} className="border-b border-line/50 last:border-0">
                  <td className="py-2.5 pr-3">
                    <span className="font-semibold text-ink">{a.label}</span>
                    <span className="ml-2 text-[12.5px] text-faint">{a.account_no}-{a.acnt_prdt_cd}</span>
                    {a.env === "vps" && <span className="ml-1.5 rounded-md bg-raised px-1.5 py-0.5 text-[11.5px] text-muted">모의</span>}
                  </td>
                  <td className="py-2.5 pr-3 text-[13px]">
                    {a.linked_portfolios.length ? <span className="text-muted">{a.linked_portfolios.join(", ")}</span> : <span className="text-faint">없음</span>}
                  </td>
                  {AE_ROWS.map((row) => {
                    const on = !!a.auto_exec[row.k];
                    return (
                      <td key={row.k} className="px-3 py-2.5 text-center">
                        <label className="inline-flex cursor-pointer items-center gap-1.5">
                          <input type="checkbox" className="h-4 w-4 accent-[#c2410c]" checked={on} disabled={busy}
                            onChange={(e) => void saveAccount(a, { [row.k]: e.target.checked }, e.target.checked ? AE_ON_CONFIRM : AE_OFF_CONFIRM)} />
                          <span className={`rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold ${on ? "bg-accent-dim text-accent" : "bg-raised text-faint"}`}>{on ? "켬" : "꺼짐"}</span>
                        </label>
                      </td>
                    );
                  })}
                  <td className="px-3 py-2.5 text-center">
                    <span className="inline-flex items-center gap-1">
                      <input className="input w-20 !py-1 text-right" disabled={busy} inputMode="decimal"
                        value={capDraft[a.id] ?? String(a.auto_exec.daily_buy_cap_pct)}
                        onChange={(e) => setCapDraft({ ...capDraft, [a.id]: e.target.value })}
                        onBlur={() => commitCap(a)} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
                      <span className="text-muted">%</span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {v && v.accounts.length > 1 && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-muted">
          <span className="font-semibold text-ink">일괄 적용</span>
          {AE_ROWS.map((row) => (
            <span key={row.k} className="inline-flex items-center gap-1">
              <span>{row.short}</span>
              <button className="btn !px-2 !py-0.5 text-[12px]" disabled={busy} onClick={() => void saveAll(row.k, true)}>모두 켬</button>
              <button className="btn !px-2 !py-0.5 text-[12px]" disabled={busy} onClick={() => void saveAll(row.k, false)}>모두 끔</button>
            </span>
          ))}
        </div>
      )}
      <p className="mt-3 text-[12.5px] leading-relaxed text-faint">
        연결 포트가 없는 계좌의 플래그는 켜 두어도 동작하지 않습니다 — 실전매매 &apos;증권사 연동&apos;에서 계좌를 포트에 연결하세요.
        여기 플래그가 유일한 스위치입니다: 켜면 다음 09:01 부터 발주하고, 끄면 그 방향의 오늘 미체결 무인 주문을 즉시 취소합니다. 하루만 수동으로 돌리려면 주문표의 &apos;이번 실행일 무인 취소&apos;를 쓰세요.
        발주 2회 연속 실패·잔고 대조 불일치·장 마감 대조의 계획 외 거래가 있으면 그 포트는 자동 정지됩니다(주문표 배너에서 다시 켜기). 09:01 이 돌지 않으면 09:15 감시가 지연 실행합니다. 모든 발주·생략·축소·실패는 주문표와 매매 로그에 남습니다.
        {msg && <span className="ml-2 text-ink">{msg}</span>}
      </p>
    </Card>
  );
}

/** 텔레그램 알림 (2026-09-07 지시) — 봇 토큰(암호화 저장·마스킹 표시)·채팅 ID('연결 확인'으로 자동)·켬/끔·보낼 항목 체크.
 *  발송 지점은 서버의 활동 로그(무인 실행·자동 승인·사전 갭 취소·정지·동기화·예수금 대조·주문), 거래 등록, 16:40 일일 현황. */
type NotifyCfg = {
  enabled: boolean; has_token: boolean; token_masked: string; chat_id: string; ready: boolean; events: Record<string, boolean>;
  last?: { sent_at?: string | null; error?: string | null; error_at?: string | null };
  categories: { key: string; label: string; desc: string; default: boolean }[];
};
function NotifySettings() {
  const [cfg, setCfg] = useState<NotifyCfg | null>(null);
  const [token, setToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const r = await apiFetch("/settings/notify");
    if (r.ok) { const j = (await r.json()) as NotifyCfg; setCfg(j); setChatId(j.chat_id); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function save(patch: Record<string, unknown>, okMsg = "저장되었습니다") {
    setBusy(true); setMsg("");
    const r = await apiFetch("/settings/notify", { method: "PUT", body: JSON.stringify(patch) });
    const j = (await r.json().catch(() => ({}))) as Partial<NotifyCfg> & { detail?: string };
    setBusy(false);
    if (!r.ok) { setMsg(j.detail ?? `저장 실패 (${r.status})`); return false; }
    setCfg((prev) => (prev ? { ...prev, ...j, categories: prev.categories } : prev));
    setMsg(okMsg);
    return true;
  }
  async function saveCreds() {
    const ok = await save({ bot_token: token.trim() || undefined, chat_id: chatId.trim() }, "봇 정보를 저장했습니다 — 아래 '연결 확인'으로 테스트 메시지를 보내 보세요");
    if (ok) setToken("");
  }
  async function test() {
    setBusy(true); setMsg("연결 확인 중…");
    const r = await apiFetch("/settings/notify/test", { method: "POST" });
    const j = (await r.json().catch(() => ({}))) as { ok?: boolean; chat_id?: string; chat_title?: string | null; detail?: string; enabled_now?: boolean };
    setBusy(false);
    if (!r.ok) { setMsg(j.detail ?? `연결 확인 실패 (${r.status})`); return; }
    setMsg(`✅ 테스트 메시지를 보냈습니다 — 채팅 ${j.chat_title ? `${j.chat_title} ` : ""}(${j.chat_id})${j.enabled_now ? " · 알림 보내기를 켰습니다" : ""}`);
    void load();
  }
  function toggleEvent(k: string, v: boolean) {
    if (!cfg) return;
    setCfg({ ...cfg, events: { ...cfg.events, [k]: v } });
    void save({ events: { [k]: v } });
  }
  if (!cfg) return <Card className="mb-4"><CardTitle>텔레그램 알림</CardTitle><p className="text-[13px] text-faint">불러오는 중…</p></Card>;
  return (
    <>
      <Card className="mb-4">
        <CardTitle right={
          <span className={`rounded-md px-2 py-0.5 text-[12px] font-semibold ${cfg.ready ? "bg-accent-dim text-accent" : "bg-raised text-faint"}`}>
            {cfg.ready ? "알림 켜짐" : cfg.has_token && cfg.chat_id ? "연결됨 · 알림 꺼짐" : "미연결"}</span>}>
          텔레그램 봇 연결 <span className="normal-case text-faint">· 매매 결과·현황을 텔레그램으로 받습니다</span>
        </CardTitle>
        {/* 연결은 됐는데 '알림 보내기'가 꺼진 상태 — 한 통도 가지 않으면서 로그도 없는 유일한 경우라 크게 보인다 (2026-09-09 운영 사례) */}
        {cfg.has_token && cfg.chat_id && !cfg.enabled && (
          <p className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-[13px] text-warn">
            <span>⚠️ 봇은 연결됐지만 <b>알림 보내기가 꺼져 있어</b> 아무 메시지도 가지 않습니다.</span>
            <button className="btn !py-1" disabled={busy} onClick={() => void save({ enabled: true }, "알림을 켰습니다")}>지금 켜기</button>
          </p>
        )}
        {/* 마지막 전송 결과 — "안 오는데 왜?" 를 여기서 바로 (2026-09-09). 실패 사유는 서버가 사람 말로 바꿔 준다(네트워크 차단·토큰 401 등) */}
        {cfg.last && (cfg.last.sent_at || cfg.last.error) && (
          <p className={`mb-3 rounded-md px-3 py-2 text-[12.5px] ${cfg.last.error ? "border border-down/40 bg-down/5 text-down" : "bg-inset text-muted"}`}>
            {cfg.last.error
              ? <>⚠️ 마지막 전송 실패 {cfg.last.error_at?.slice(5, 16).replace("T", " ")} — {cfg.last.error}{cfg.last.sent_at ? <span className="text-faint"> · 마지막 성공 {cfg.last.sent_at.slice(5, 16).replace("T", " ")}</span> : null}</>
              : <>✓ 마지막 전송 성공 {cfg.last.sent_at?.slice(5, 16).replace("T", " ")}</>}
          </p>
        )}
        <ol className="mb-3 grid gap-1 text-[13px] leading-relaxed text-muted">
          <li>① 텔레그램에서 <b className="text-ink">@BotFather</b> 에게 <code>/newbot</code> 을 보내 봇을 만들고 토큰(예: <code>123456789:AAH…</code>)을 복사합니다.</li>
          <li>② 아래에 토큰을 붙여넣고 저장합니다. 토큰은 암호화되어 저장되고 화면에는 마스킹으로만 보입니다.</li>
          <li>③ 텔레그램에서 방금 만든 봇을 찾아 <b className="text-ink">아무 메시지</b>나 보낸 뒤 <b className="text-ink">연결 확인</b>을 누르면 채팅 ID가 자동으로 채워지고 테스트 메시지가 옵니다.</li>
        </ol>
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
          <label className="grid gap-1 text-[13px] text-faint">봇 토큰
            <CredentialInput name="tg_token" value={token} onChange={setToken} stored={cfg.has_token ? cfg.token_masked : undefined} placeholder="123456789:AAH…" /></label>
          <label className="grid gap-1 text-[13px] text-faint">채팅 ID <span className="text-[11.5px]">(비워 두면 연결 확인이 채움)</span>
            <input className="input" value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="자동" /></label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[13.5px]">
          <button className="btn btn-primary !py-1.5" disabled={busy || (!token.trim() && !cfg.has_token)} onClick={() => void saveCreds()}>저장</button>
          <button className="btn !py-1.5" disabled={busy || !cfg.has_token} onClick={() => void test()}>연결 확인 · 테스트 메시지</button>
          <label className="ml-2 flex items-center gap-2">
            <input type="checkbox" className="h-4 w-4 accent-[#c2410c]" checked={cfg.enabled} disabled={busy}
              onChange={(e) => void save({ enabled: e.target.checked }, e.target.checked ? "알림을 켰습니다" : "알림을 껐습니다")} />
            <span className="font-semibold text-ink">알림 보내기</span>
          </label>
          {cfg.has_token && (
            <button className="text-[12.5px] text-faint hover:text-down" disabled={busy}
              onClick={() => { if (window.confirm("저장된 봇 토큰을 삭제할까요? 알림이 중단됩니다.")) void save({ clear_token: true, enabled: false }, "토큰을 삭제했습니다"); }}>토큰 삭제</button>
          )}
          {msg && <span className="text-muted">{msg}</span>}
        </div>
      </Card>
      <Card className="mb-4">
        <CardTitle>보낼 메시지 항목 <span className="normal-case text-faint">· 체크한 항목만 발송됩니다 — 알림이 켜져 있고 연결이 끝난 뒤부터</span></CardTitle>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {cfg.categories.map((c) => (
            <label key={c.key} className="flex items-start gap-3 rounded-xl border border-line bg-inset p-3">
              <input type="checkbox" className="mt-1 h-4 w-4 accent-[#c2410c]" checked={!!cfg.events[c.key]} disabled={busy}
                onChange={(e) => toggleEvent(c.key, e.target.checked)} />
              <span className="grid gap-0.5">
                <span className="text-[14px] font-semibold text-ink">{c.label}</span>
                <span className="text-[12.5px] leading-relaxed text-muted">{c.desc}</span>
              </span>
            </label>
          ))}
        </div>
        <p className="mt-3 text-[12px] leading-relaxed text-faint">
          메시지는 서버가 기록을 남기는 시점에 바로 보냅니다(무인 실행 09:01, 사전 갭 취소 08:57, 장 마감 동기화 15:45, 자동 승인 16:45, 일일 현황 16:40).
          전송 실패는 매매 로그에 "알림 전송 실패"로 남고 본 작업은 계속됩니다.
        </p>
      </Card>
    </>
  );
}

