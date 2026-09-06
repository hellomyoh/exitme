"use client";

/** 매매 로그 (2026-09-06 지시 "로깅 기능") — 거래 원장·증권사 주문 상태·실행/동기화 이벤트를 한 표에 최신순으로.
 *  실패·경고("경고 이상만" 필터)도 여기서 확인한다. 원천 셋을 서버(GET /logs)가 합친다 — 중복 기록 없음. */
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ensureSession } from "../../lib/api";
import { Badge, Card, CardTitle, EmptyState, PageTitle } from "../../components/ui";

type Level = "info" | "warn" | "error";
type Item = {
  at: string; type: "trade" | "order" | "event"; kind: string; kind_ko: string; level: Level;
  portfolio_id: number | null; portfolio: string | null; text: string; detail: string | null; ref: string; plan_date?: string | null;
};
type Resp = { days: number; total: number; counts: Record<Level, number>; items: Item[]; portfolios: { id: number; name: string; market: string }[] };

const TYPE_KO: Record<Item["type"], string> = { trade: "거래", order: "주문", event: "이벤트" };
const TYPE_TONE: Record<Item["type"], "accent" | "default" | "ok"> = { trade: "accent", order: "default", event: "ok" };
const LEVEL_KO: Record<Level, string> = { info: "정상", warn: "경고", error: "오류" };
const DAYS = [7, 30, 90, 365];

export default function LogsPage() {
  return <Suspense fallback={null}><LogsInner /></Suspense>;
}

function LogsInner() {
  const router = useRouter();
  const [days, setDays] = useState(30);
  const [type, setType] = useState<"all" | Item["type"]>("all");
  const [level, setLevel] = useState<"all" | "warn" | "error">("all");
  const [pid, setPid] = useState<string>("");
  const [q, setQ] = useState("");
  const [resp, setResp] = useState<Resp | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setBusy(true); setErr("");
    const params = new URLSearchParams({ days: String(days), type, level });
    if (pid) params.set("portfolio_id", pid);
    if (q.trim()) params.set("q", q.trim());
    const r = await apiFetch(`/logs?${params.toString()}`);
    setBusy(false);
    if (!r.ok) { setErr(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `조회 실패 (${r.status})`); return; }
    setResp((await r.json()) as Resp);
  }, [days, type, level, pid, q]);

  useEffect(() => {
    void ensureSession().then((ok) => { if (!ok) { router.push("/login"); return; } void load(); });
  }, [load, router]);

  const fmtAt = (s: string) => s.slice(5, 16).replace("T", " ");   // "MM-DD HH:mm" (서버가 KST 로 맞춰 준다)
  const rowCls = (lv: Level) => lv === "error" ? "bg-down/5" : lv === "warn" ? "bg-warn/5" : "";
  const dot = (lv: Level) => lv === "error" ? "bg-down" : lv === "warn" ? "bg-warn" : "bg-ok";

  return (
    <main>
      <PageTitle title="매매 로그" sub="거래 원장 · 증권사 주문 상태 · 무인 실행/동기화 이벤트 — 실패와 경고도 여기서 확인합니다" />

      <Card className="mb-4">
        <CardTitle right={<button className="btn !py-1.5 text-[13px]" disabled={busy} onClick={() => void load()}>{busy ? "조회 중…" : "새로고침"}</button>}>
          필터 <span className="normal-case text-faint">· 거래는 실행 시각, 주문은 마지막 상태 변경 시각, 이벤트는 발생 시각 기준</span>
        </CardTitle>
        <div className="flex flex-wrap items-end gap-3 text-[13.5px]">
          <div className="grid gap-1 text-[13px] text-faint">기간
            <div className="flex gap-1">
              {DAYS.map((d) => (
                <button key={d} onClick={() => setDays(d)}
                  className={`rounded-lg border px-3 py-1.5 transition-colors ${days === d ? "border-accent bg-accent-dim font-semibold text-ink" : "border-line bg-inset text-muted hover:border-line-strong"}`}>
                  {d === 365 ? "1년" : `${d}일`}
                </button>
              ))}
            </div>
          </div>
          <label className="grid gap-1 text-[13px] text-faint">유형
            <select className="input !py-2" value={type} onChange={(e) => setType(e.target.value as typeof type)}>
              <option value="all">전체</option><option value="trade">거래</option><option value="order">주문</option><option value="event">이벤트</option>
            </select></label>
          <label className="grid gap-1 text-[13px] text-faint">수준
            <select className="input !py-2" value={level} onChange={(e) => setLevel(e.target.value as typeof level)}>
              <option value="all">전체</option><option value="warn">경고 이상만 (실패 포함)</option><option value="error">오류만</option>
            </select></label>
          <label className="grid gap-1 text-[13px] text-faint">실전매매
            <select className="input !py-2" value={pid} onChange={(e) => setPid(e.target.value)}>
              <option value="">전체</option>
              {(resp?.portfolios ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}{p.market === "US" ? " (미국)" : ""}</option>)}
            </select></label>
          <label className="grid gap-1 text-[13px] text-faint">검색
            <input className="input w-48 !py-2" placeholder="종목·메시지·포트" value={q}
              onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void load()} /></label>
        </div>
        {resp && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <span>최근 {resp.days}일 <b className="text-ink">{resp.total.toLocaleString()}</b>건</span>
            <button className={`rounded-md border px-2 py-0.5 ${level === "warn" ? "border-warn bg-warn/10" : "border-line"}`} onClick={() => setLevel(level === "warn" ? "all" : "warn")}>
              <span className="mr-1 inline-block h-2 w-2 rounded-full bg-warn" />경고 {resp.counts.warn.toLocaleString()}</button>
            <button className={`rounded-md border px-2 py-0.5 ${level === "error" ? "border-down bg-down/10" : "border-line"}`} onClick={() => setLevel(level === "error" ? "all" : "error")}>
              <span className="mr-1 inline-block h-2 w-2 rounded-full bg-down" />오류 {resp.counts.error.toLocaleString()}</button>
            {resp.total > resp.items.length && <span className="text-faint">· 최신 {resp.items.length.toLocaleString()}건만 표시 — 기간이나 필터를 좁히세요</span>}
            {err && <span className="text-down">{err}</span>}
          </div>
        )}
      </Card>

      {resp && resp.items.length === 0 && (
        <EmptyState icon="🗒️" title="해당하는 기록이 없습니다"
          desc="거래를 등록하거나 예약주문·무인 실행을 쓰면 여기에 시간순으로 쌓입니다. 실패한 주문·조회 오류는 '경고 이상만' 필터로 모아 볼 수 있습니다." />
      )}
      {resp && resp.items.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full whitespace-nowrap text-[13.5px]">
              <thead>
                <tr className="border-b border-line text-left text-[12px] text-faint">
                  <th className="pb-2 pr-3 font-medium">시각</th>
                  <th className="pb-2 pr-3 font-medium">실전매매</th>
                  <th className="pb-2 pr-3 font-medium">유형</th>
                  <th className="pb-2 pr-3 font-medium">종류</th>
                  <th className="pb-2 font-medium">내용</th>
                </tr>
              </thead>
              <tbody>
                {resp.items.map((it) => (
                  <tr key={it.ref} className={`border-b border-line/50 align-top last:border-0 ${rowCls(it.level)}`}>
                    <td className="py-2 pr-3 tabular-nums text-muted">{fmtAt(it.at)}</td>
                    <td className="py-2 pr-3 text-muted">{it.portfolio ?? <span className="text-faint">—</span>}</td>
                    <td className="py-2 pr-3"><Badge tone={TYPE_TONE[it.type]}>{TYPE_KO[it.type]}</Badge></td>
                    <td className="py-2 pr-3">
                      <span className="inline-flex items-center gap-1.5" title={LEVEL_KO[it.level]}>
                        <span className={`inline-block h-2 w-2 rounded-full ${dot(it.level)}`} />{it.kind_ko}
                      </span>
                    </td>
                    <td className="max-w-[680px] whitespace-normal py-2 leading-relaxed">
                      <span className={it.level === "error" ? "text-down" : "text-ink"}>{it.text}</span>
                      {it.detail && <div className="text-[12.5px] text-faint">{it.detail}</div>}
                      {it.plan_date && <div className="text-[11.5px] text-faint">실행일 {it.plan_date}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      <p className="mt-3 text-[12px] text-faint">
        거래는 거래 원장(수동 등록·증권사 가져오기·예수금 보정), 주문은 예약주문·무인 실행 줄의 현재 상태, 이벤트는 무인 실행·사전 갭 취소·장 마감 동기화·예수금 대조 등의 과정 기록입니다.
        삭제한 거래는 "거래 삭제" 이벤트로 남습니다.
      </p>
    </main>
  );
}
