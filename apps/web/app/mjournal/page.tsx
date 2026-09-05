"use client";

/** 주식 매매일지 — 수동 기록 (2026-09-05 지시, 스프레드시트 대체).
 *  일지 생성 시 이름·기본 종목·증권사·요율을 받고, 매일 입력은 종목·구분·수량·단가만.
 *  한 일지에 여러 종목 입력 가능(드롭다운) — 실현손익·수익률·보유기간·비용은 종목별 FIFO 로
 *  서버가 계산. 그래프(보유 비중 도넛·누적 실현손익 라인)는 통계 카드 아래 배치.
 *  일지 간 완전 분리(2026-09-05 지시): 화면의 모든 숫자·그래프·종목 목록은 선택한 일지 하나의 것만 쓴다
 *  (예전 '전체 현황'은 전 일지 합산이라 새 일지에 다른 일지 종목이 보이는 것처럼 오해를 낳았다). */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, ensureSession } from "../../lib/api";
import { Card, CardTitle, EmptyState, PageTitle, Stat } from "../../components/ui";

type JournalMeta = { id: number; name: string; symbol: string; broker: string; closed_at?: string | null };
type Holding = { symbol: string; qty: number; avg_price: number; cost: number; realized: number; matched: number; return_pct: number | null };
type Row = {
  id: number; symbol: string; side: "buy" | "sell"; buy_date: string | null; sell_date: string | null;
  hold_days: number | null; realized: number | null; return_pct: number | null;
  price: number; qty: number; cost: number | null; amount: number; reason: string | null; error?: string;
  source: "manual" | "broker"; code: string | null;   // 증권사 가져오기 표시 (0018)
};
type Acct = { id: number; label: string; account_no: string; acnt_prdt_cd: string; env: string };
type ImportItem = { broker_ref: string; date: string; code: string; name: string; symbol: string; match: string;
  side: "buy" | "sell"; qty: number; price: number; amount: number; status: string; warnings: string[] };
type ImportResult = { range: [string, string]; dry_run: boolean; fetched: number; added: number; skipped: number;
  new_symbols: string[]; items: ImportItem[] };
type Detail = JournalMeta & {
  fee_rate: number; tax_rate: number; rows: Row[]; symbols: string[];
  summary: { realized: number; sell_amount: number; buy_amount: number; cost: number; return_pct: number | null };
  holdings: Holding[];
  series: Record<string, { date: string; value: number }[]>;   // 종목별 누적 실현손익 (이 일지만)
  linked_account: { id: number; label: string; account_no: string; env: string } | null;   // 연결 계좌 (0018)
  closed_at?: string | null;   // 청산 시각 (0020) — 있으면 기록 추가 불가, 대시보드 제외
};

/** 증권사 체결 가져오기 (0018, 2026-09-05 지시) — 검토 문서 권고안.
 *  계좌는 설정에서 등록한 것 중 선택해 연결하고, 체결은 미리보기(종목 매칭·경고 확인) → 등록 두 단계.
 *  조회 전용이며 수동 기록을 자동으로 고치지 않는다 — 보유 초과 매도·수동 중복은 경고만. */
function BrokerImport({ detail, accts, onChanged }: { detail: Detail; accts: Acct[]; onChanged: () => void }) {
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ImportResult | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setRes(null); setErr(""); }, [detail.id]);

  async function link(id: number | null) {
    setErr("");
    const r = await apiFetch(`/mjournals/${detail.id}/broker`, { method: "PUT", body: JSON.stringify({ credential_id: id }) });
    if (r.ok) { setRes(null); onChanged(); }
    else setErr(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `연결 실패 (${r.status})`);
  }
  async function run(dry: boolean) {
    setBusy(true); setErr("");
    const r = await apiFetch(`/mjournals/${detail.id}/import-fills?days=${days}&dry_run=${dry}`, { method: "POST" });
    const body = (await r.json().catch(() => ({}))) as ImportResult & { detail?: string };
    setBusy(false);
    if (!r.ok) { setErr(body.detail ?? `조회 실패 (${r.status})`); return; }
    setRes(body);
    if (!dry) onChanged();
  }
  const pending = res?.dry_run ? res.items.filter((i) => i.status === "등록 예정").length : 0;
  const linked = detail.linked_account;
  // 잔고 기준 기초 보유 등록 (2026-09-05) — 체결 기간 이전에 산 보유분(예: 삼성전자 11주)을 잔고로 대조해 부족분만 넣는다
  type Hold = { code: string; name: string; qty: number; avg_price: number; buy_amount: number; price: number; eval_amount: number;
    symbol: string; match: string; journal_qty: number; diff: number };
  const [hold, setHold] = useState<{ date: string; items: Hold[]; note: string } | null>(null);
  const [holdSel, setHoldSel] = useState<Set<string>>(new Set());
  const [holdDate, setHoldDate] = useState(new Date().toISOString().slice(0, 10));
  const [holdMsg, setHoldMsg] = useState("");
  useEffect(() => { setHold(null); setHoldMsg(""); }, [detail.id]);
  async function loadHoldings() {
    setBusy(true); setErr(""); setHoldMsg("");
    const r = await apiFetch(`/mjournals/${detail.id}/broker-holdings`);
    const body = (await r.json().catch(() => ({}))) as { date: string; items: Hold[]; note: string; detail?: string };
    setBusy(false);
    if (!r.ok) { setErr(body.detail ?? `잔고 조회 실패 (${r.status})`); return; }
    setHold(body);
    setHoldSel(new Set(body.items.filter((i) => i.diff > 0).map((i) => i.code)));
  }
  const holdPending = (hold?.items ?? []).filter((i) => i.diff > 0 && holdSel.has(i.code));
  async function importHoldings() {
    if (holdPending.length === 0) return;
    setBusy(true); setErr("");
    const r = await apiFetch(`/mjournals/${detail.id}/import-holdings`, { method: "POST", body: JSON.stringify({
      trade_date: holdDate, items: holdPending.map((i) => ({ code: i.code, name: i.name, qty: i.diff, price: i.avg_price })) }) });
    const body = (await r.json().catch(() => ({}))) as { added?: number; skipped?: number; detail?: string };
    setBusy(false);
    if (!r.ok) { setErr(body.detail ?? `등록 실패 (${r.status})`); return; }
    setHoldMsg(`기초 보유 ${body.added ?? 0}건 등록${(body.skipped ?? 0) > 0 ? ` · ${body.skipped}건은 같은 날 이미 등록` : ""}`);
    onChanged();
    void loadHoldings();
  }
  return (
    <Card className="mb-4">
      <CardTitle>증권사 체결 가져오기 <span className="normal-case text-faint">
        · 조회 전용 · 수동 기록은 고치지 않고 경고만 표시 · 수수료·세금은 일지 요율로 추정</span></CardTitle>
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-[12.5px] text-faint">연결 계좌
          <select className="input !py-2" value={linked?.id ?? ""}
            onChange={(e) => void link(e.target.value ? Number(e.target.value) : null)}>
            <option value="">연결 안 함</option>
            {accts.map((a) => (
              <option key={a.id} value={a.id}>{a.label} · {a.account_no}-{a.acnt_prdt_cd}{a.env === "vps" ? " · 모의" : ""}</option>
            ))}
          </select></label>
        {accts.length === 0 && (
          <span className="pb-2 text-[12.5px] text-faint">등록된 계좌가 없습니다 —{" "}
            <Link href="/settings?tab=broker" className="font-semibold text-accent">일반 설정 › 증권사 계좌</Link>에서 먼저 등록하세요.</span>
        )}
        {linked && (<>
          <label className="grid gap-1 text-[12.5px] text-faint">기간
            <select className="input !py-2" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 30, 90, 180, 365].map((d) => <option key={d} value={d}>최근 {d}일</option>)}
            </select></label>
          <button className="btn !py-2" disabled={busy} onClick={() => void run(true)}>{busy ? "조회 중…" : "미리보기"}</button>
          <button className="btn btn-primary !py-2" disabled={busy || pending === 0} title={pending === 0 ? "먼저 미리보기로 확인하세요" : ""}
            onClick={() => void run(false)}>등록{pending > 0 ? ` (${pending}건)` : ""}</button>
        </>)}
      </div>
      {err && <p className="mt-2 text-[13.5px] text-up">{err}</p>}
      {res && (
        <div className="mt-3">
          <p className="text-[13px] text-muted">
            {res.range[0]} ~ {res.range[1]} · 조회 {res.fetched}건 · {res.dry_run ? `등록 예정 ${pending}건` : `등록됨 ${res.added}건`} · 이미 등록 {res.skipped}건
            {res.new_symbols.length > 0 && <> · 새 종목: <b className="text-ink">{res.new_symbols.join(", ")}</b></>}
          </p>
          {res.items.length === 0 ? (
            <p className="mt-1 text-[13.5px] text-faint">이 기간에 체결이 없습니다 — 그 전에 산 보유분은 아래 &quot;현재 잔고로 기초 보유 등록&quot;을 쓰세요.</p>
          ) : (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full whitespace-nowrap text-[13px]">
                <thead><tr className="border-b border-line text-left text-[12px] text-faint">
                  <th className="pb-1.5 font-medium">일자</th><th className="pb-1.5 font-medium">종목 (일지 기준)</th>
                  <th className="pb-1.5 font-medium">구분</th><th className="pb-1.5 text-right font-medium">수량</th>
                  <th className="pb-1.5 text-right font-medium">단가</th><th className="pb-1.5 pl-3 font-medium">상태</th>
                  <th className="pb-1.5 pl-3 font-medium">경고</th>
                </tr></thead>
                <tbody>
                  {res.items.map((i) => (
                    <tr key={i.broker_ref} className="border-b border-line/50 last:border-0">
                      <td className="py-1.5">{i.date}</td>
                      <td className="py-1.5 font-semibold">{i.symbol}
                        {i.match !== "코드" && <span className="ml-1 text-[11.5px] font-normal text-faint">← {i.name} ({i.code}){i.match === "새 종목" ? " · 새 종목" : ""}</span>}</td>
                      <td className={`py-1.5 font-bold ${i.side === "buy" ? "text-up" : "text-down"}`}>{i.side === "buy" ? "매수" : "매도"}</td>
                      <td className="table-num py-1.5">{i.qty.toLocaleString()}</td>
                      <td className="table-num py-1.5">{i.price.toLocaleString()}</td>
                      <td className={`py-1.5 pl-3 ${i.status === "이미 등록됨" ? "text-faint" : "text-ink"}`}>{i.status}</td>
                      <td className="py-1.5 pl-3 text-[12.5px] text-up">{i.warnings.map((w, k) => <div key={k}>⚠ {w}</div>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      {linked && (
        <div className="mt-4 border-t border-line pt-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="mr-2">
              <div className="text-[13px] font-semibold text-muted">현재 잔고로 기초 보유 등록</div>
              <div className="text-[12px] text-faint">체결 가져오기 기간 <b className="text-ink">이전에 산 보유분</b>은 여기서 잔고 기준으로 넣습니다 — 계좌 평단·등록일 기준 매수 1건(근사)</div>
            </div>
            <label className="grid gap-1 text-[12.5px] text-faint">등록일
              <input type="date" className="input !py-2" value={holdDate} onChange={(e) => setHoldDate(e.target.value)} /></label>
            <button className="btn !py-2" disabled={busy} onClick={() => void loadHoldings()}>{busy ? "조회 중…" : "잔고 조회"}</button>
            <button className="btn btn-primary !py-2" disabled={busy || holdPending.length === 0}
              title={!hold ? "먼저 잔고를 조회하세요" : holdPending.length === 0 ? "등록할 부족분이 없습니다" : ""}
              onClick={() => void importHoldings()}>선택 등록{holdPending.length > 0 ? ` (${holdPending.length}건)` : ""}</button>
            {holdMsg && <span className="text-[13px] text-muted">{holdMsg}</span>}
          </div>
          {hold && (
            <div className="mt-2 overflow-x-auto">
              {hold.items.length === 0 ? (
                <p className="text-[13.5px] text-faint">이 계좌에 보유 종목이 없습니다.</p>
              ) : (
                <table className="w-full whitespace-nowrap text-[13px]">
                  <thead><tr className="border-b border-line text-left text-[12px] text-faint">
                    <th className="pb-1.5 pr-1 font-medium">
                      <input type="checkbox" className="h-4 w-4" disabled={!hold.items.some((i) => i.diff > 0)}
                        checked={hold.items.filter((i) => i.diff > 0).every((i) => holdSel.has(i.code)) && hold.items.some((i) => i.diff > 0)}
                        onChange={(e) => setHoldSel(e.target.checked ? new Set(hold.items.filter((i) => i.diff > 0).map((i) => i.code)) : new Set())} />
                    </th>
                    <th className="pb-1.5 font-medium">종목 (일지 기준)</th>
                    <th className="pb-1.5 text-right font-medium">계좌 보유</th>
                    <th className="pb-1.5 text-right font-medium">일지 보유</th>
                    <th className="pb-1.5 text-right font-medium">등록 수량</th>
                    <th className="pb-1.5 text-right font-medium">계좌 평단</th>
                    <th className="pb-1.5 text-right font-medium">현재가</th>
                    <th className="pb-1.5 pl-3 font-medium">상태</th>
                  </tr></thead>
                  <tbody>
                    {hold.items.map((i) => (
                      <tr key={i.code} className={`border-b border-line/50 last:border-0 ${i.diff === 0 ? "text-faint" : ""}`}>
                        <td className="py-1.5 pr-1">
                          {i.diff > 0 ? (
                            <input type="checkbox" className="h-4 w-4" checked={holdSel.has(i.code)}
                              onChange={() => setHoldSel((prev) => { const n = new Set(prev); if (n.has(i.code)) n.delete(i.code); else n.add(i.code); return n; })} />
                          ) : <span className="inline-block h-4 w-4 text-center text-[12px]">✓</span>}
                        </td>
                        <td className="py-1.5 font-semibold">{i.symbol}
                          {i.match !== "코드" && <span className="ml-1 text-[11.5px] font-normal text-faint">← {i.name} ({i.code}){i.match === "새 종목" ? " · 새 종목" : ""}</span>}</td>
                        <td className="table-num py-1.5">{i.qty.toLocaleString()}주</td>
                        <td className="table-num py-1.5">{i.journal_qty.toLocaleString()}주</td>
                        <td className="table-num py-1.5 font-bold">{i.diff > 0 ? `+${i.diff.toLocaleString()}주` : "—"}</td>
                        <td className="table-num py-1.5">{i.avg_price.toLocaleString()}</td>
                        <td className="table-num py-1.5">{i.price.toLocaleString()}</td>
                        <td className="py-1.5 pl-3 text-[12.5px]">{i.diff > 0 ? "일지에 부족 — 등록 대상" : i.journal_qty > i.qty ? `일지가 ${(i.journal_qty - i.qty).toLocaleString()}주 더 많음` : "일치"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="mt-1.5 text-[12px] text-faint">{hold.note}</p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

const fm = (v: number) => `${v.toLocaleString()}원`;
const OV_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

/** 이 일지 현황 그래프 (2026-09-05 지시) — 통계 카드 아래 배치. 선택한 일지 하나의 데이터만 쓴다.
 *  좌: 종목별 보유 비중 도넛(취득원가 기준 — 시세 미연동 명시), 우: 종목별 누적 실현손익 라인. */
function Overview({ detail }: { detail: Detail }) {
  const [tip, setTip] = useState<{ x: number; y: number; title: string; color: string;
    rows: { label: string; value: string; tone?: "up" | "down" }[] } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const api = useRef<IChartApi | null>(null);
  const held = detail.holdings;                    // 서버가 원가 내림차순으로 준다
  const total = held.reduce((a, h) => a + h.cost, 0);
  // 색은 일지의 종목 목록 순서로 고정 — 도넛과 라인이 같은 종목에 같은 색 (필터에 따라 바뀌지 않음)
  const symColor = (sym: string) => OV_COLORS[Math.max(detail.symbols.indexOf(sym), 0) % OV_COLORS.length];
  const withSeries = Object.entries(detail.series)
    .filter(([, pts]) => pts.length >= 1)
    .map(([symbol, pts]) => ({ symbol, pts, realized: pts[pts.length - 1].value }));

  useEffect(() => {
    if (!chartRef.current || withSeries.length === 0) return;
    api.current?.remove();
    const korUnit = (v: number) => {  // 축 금액 억/만 자동 단위 (2026-09-05 지시)
      const a = Math.abs(v);
      if (a >= 1e8) return `${(v / 1e8).toFixed(a >= 1e9 ? 0 : 1)}억`;
      if (a >= 1e4) return `${Math.round(v / 1e4).toLocaleString()}만`;
      return `${Math.round(v).toLocaleString()}`;
    };
    const chart = createChart(chartRef.current, {
      localization: { priceFormatter: korUnit },
      layout: { background: { color: "transparent" }, textColor: "#9aa1ad", attributionLogo: false, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: "#eef0f3" } },
      height: 190, autoSize: true, rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
    });
    api.current = chart;
    for (const it of withSeries) {
      const pts = it.pts.map((p) => ({ time: p.date, value: p.value }));
      if (pts.length === 1) {  // 점 하나로는 라인이 안 보임 — 전날 0 에서 출발
        const d0 = new Date(pts[0].time);
        d0.setDate(d0.getDate() - 1);
        pts.unshift({ time: d0.toISOString().slice(0, 10), value: 0 });
      }
      chart.addSeries(LineSeries, { color: symColor(it.symbol), lineWidth: 2, title: it.symbol }).setData(pts);
    }
    chart.timeScale().fitContent();
    return () => { try { api.current?.remove(); } catch { /* noop */ } api.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id, JSON.stringify(detail.series), JSON.stringify(detail.symbols)]);

  if (held.length === 0 && withSeries.length === 0) return null;
  const R = 52, r0 = 30, C = 64;
  let angle = -Math.PI / 2;
  const paths = held.map((h) => {
    const frac = h.cost / total;
    const gap = 2 / R;
    const a0 = angle + gap / 2, a1 = angle + Math.max(frac * Math.PI * 2 - gap / 2, 0.006);
    angle += frac * Math.PI * 2;
    const pt = (a: number, rad: number) => `${C + rad * Math.cos(a)},${C + rad * Math.sin(a)}`;
    const la = a1 - a0 > Math.PI ? 1 : 0;
    return { h, frac, d: `M${pt(a0, R)} A${R},${R} 0 ${la} 1 ${pt(a1, R)} L${pt(a1, r0)} A${r0},${r0} 0 ${la} 0 ${pt(a0, r0)} Z` };
  });
  return (
    <Card className="relative mb-4">
      <CardTitle>{detail.name} 현황 <span className="normal-case text-faint">· 이 일지만 · 보유 비중은 취득원가, 수익 라인은 종목별 누적 실현손익 (시세 미연동)</span></CardTitle>
      <div className="grid gap-6 lg:grid-cols-[auto_1fr]">
        <div className="flex items-center gap-4">
          {held.length > 0 && (
            <svg viewBox="0 0 128 128" className="h-32 w-32 shrink-0" role="img" aria-label="종목별 보유 비중">
              {paths.map(({ h, frac, d }, i) => (
                <path key={i} d={d} fill={symColor(h.symbol)} opacity={0.85}
                  onMouseMove={(e) => {
                    const box = (e.currentTarget.ownerSVGElement!.closest(".card") as HTMLElement).getBoundingClientRect();
                    const pct = h.matched > 0 ? h.realized / h.matched : null;  // 실현 수익률 (매도분 원가 대비)
                    setTip({ x: e.clientX - box.left + 12, y: e.clientY - box.top + 12,
                             title: h.symbol, color: symColor(h.symbol),
                             rows: [
                               { label: "보유", value: `${h.qty.toLocaleString()}주` },
                               { label: "원가 · 비중", value: `${fm(h.cost)} · ${(frac * 100).toFixed(1)}%` },
                               { label: "실현 수익률", value: pct != null ? `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(1)}%` : "— (매도 없음)",
                                 tone: pct == null ? undefined : pct >= 0 ? "up" : "down" },
                             ] });
                  }}
                  onMouseLeave={() => setTip(null)} />
              ))}
            </svg>
          )}
          <div className="grid content-center gap-1 text-[13px]">
            {held.map((h) => (
              <span key={h.symbol} className="inline-flex items-center gap-1.5 text-muted"
                title={`${h.qty.toLocaleString()}주 · 원가 ${fm(h.cost)}`}>
                <i className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: symColor(h.symbol) }} />
                {h.symbol} <b className="text-ink">{total > 0 ? ((h.cost / total) * 100).toFixed(0) : 0}%</b>
              </span>
            ))}
            {held.length === 0 && <span className="text-faint">현재 보유 없음</span>}
          </div>
        </div>
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
            <span className="font-semibold text-faint">누적 실현손익 (종목별)</span>
            {withSeries.map((it) => (
              <span key={it.symbol} className="inline-flex items-center gap-1.5">
                <i className="h-2 w-2 rounded-full" style={{ background: symColor(it.symbol) }} />
                {it.symbol} <b className={it.realized > 0 ? "text-up" : it.realized < 0 ? "text-down" : "text-ink"}>
                  {it.realized >= 0 ? "+" : ""}{it.realized.toLocaleString()}원</b>
              </span>
            ))}
            {withSeries.length === 0 && <span className="text-faint">매도(실현) 기록이 생기면 추이가 그려집니다.</span>}
          </div>
          {withSeries.length > 0 && <div ref={chartRef} className="h-[190px]" />}
        </div>
      </div>
      {tip && (
        <div className="pointer-events-none absolute z-20 min-w-44 rounded-xl border border-line bg-surface shadow-lg"
          style={{ left: tip.x, top: tip.y }}>
          <div className="flex items-center gap-1.5 border-b border-line px-3 py-2 text-[13px] font-bold">
            <i className="h-2.5 w-2.5 rounded-sm" style={{ background: tip.color }} />{tip.title}
          </div>
          <div className="grid gap-1 px-3 py-2 text-[12.5px]">
            {tip.rows.map((r, i) => (
              <div key={i} className="flex justify-between gap-4">
                <span className="text-faint">{r.label}</span>
                <b className={r.tone === "up" ? "text-up" : r.tone === "down" ? "text-down" : "text-ink"}>{r.value}</b>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

export default function MJournalPageWrapper() {
  return <Suspense fallback={null}><MJournalPage /></Suspense>;
}

function MJournalPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const spJid = sp?.get("jid");
  const spNew = sp?.get("new") === "1";
  const [list, setList] = useState<JournalMeta[]>([]);
  const [jid, setJid] = useState<number | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [showNew, setShowNew] = useState(spNew);
  const [nf, setNf] = useState({ name: "", symbol: "", broker: "", fee: "0.015", tax: "0.23" });
  const NEW_SYM = "__new__";
  const [ef, setEf] = useState({ side: "buy", symbol: "", newSymbol: "", qty: "", price: "",
    date: new Date().toISOString().slice(0, 10), reason: "" });
  const [msg, setMsg] = useState("");
  const [accts, setAccts] = useState<Acct[]>([]);          // 설정에 등록된 증권사 계좌 (0018)
  const [showImport, setShowImport] = useState(false);

  const load = useCallback(async (selected: number | null) => {
    const r = await apiFetch("/mjournals");
    if (!r.ok) return;
    void apiFetch("/broker/accounts").then(async (a) => {
      if (a.ok) setAccts(((await a.json()) as { items: Acct[] }).items);
    });
    const items = ((await r.json()) as { items: JournalMeta[] }).items;
    setList(items);
    const id = selected ?? items[0]?.id ?? null;
    setJid(id);
    if (id !== null) {
      const d = await apiFetch(`/mjournals/${id}`);
      if (d.ok) {
        const dd = (await d.json()) as Detail;
        setDetail(dd);
        setEf((prev) => ({ ...prev, symbol: dd.symbols.includes(prev.symbol) ? prev.symbol : dd.symbol, newSymbol: "" }));
      }
    } else setDetail(null);
  }, []);

  useEffect(() => { setShowImport(detail?.linked_account != null); }, [detail?.id, detail?.linked_account]);

  useEffect(() => {
    // 서브메뉴(?jid=)·새 일지(?new=1) 파라미터 반영 — 메뉴 클릭 시마다 리로드 (2026-09-05)
    void ensureSession().then((ok) => {
      if (!ok) { router.push("/login"); return; }
      setShowNew(spNew);
      void load(spJid ? Number(spJid) : null);
    });
  }, [load, router, spJid, spNew]);

  async function createJournal() {
    setMsg("");
    const r = await apiFetch("/mjournals", { method: "POST", body: JSON.stringify({
      name: nf.name.trim(), symbol: nf.symbol.trim(), broker: nf.broker.trim(),
      fee_rate: Number(nf.fee) / 100, tax_rate: Number(nf.tax) / 100 }) });
    if (r.ok) {
      const { id } = (await r.json()) as { id: number };
      setShowNew(false); setNf({ name: "", symbol: "", broker: "", fee: "0.015", tax: "0.23" });
      void load(id);
    } else setMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `생성 실패 (${r.status})`);
  }

  async function addEntry() {
    if (jid === null) return;
    setMsg("");
    const symbol = ef.symbol === NEW_SYM ? ef.newSymbol.trim() : ef.symbol;
    if (!symbol) { setMsg("종목명을 입력하세요"); return; }
    const r = await apiFetch(`/mjournals/${jid}/entries`, { method: "POST", body: JSON.stringify({
      side: ef.side, qty: Number(ef.qty), price: Number(ef.price), symbol,
      trade_date: ef.date, reason: ef.reason.trim() || undefined }) });
    if (r.ok) { setEf({ ...ef, symbol, newSymbol: "", qty: "", price: "", reason: "" }); void load(jid); }
    else setMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `등록 실패 (${r.status})`);
  }

  return (
    <main>
      <PageTitle title="주식 매매일지" sub="종목별 수동 매매 기록 — 실현손익·수익률·보유기간·비용은 자동 계산됩니다 (종목별 FIFO)" />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* 탭 선택 시 생성 폼 닫기 — 폼이 열린 채 본문이 계속 숨어 화면이 깨져 보이던 문제 (2026-09-05) */}
        {list.slice().sort((a, b) => Number(!!a.closed_at) - Number(!!b.closed_at)).map((j) => (
          <button key={j.id} onClick={() => { setShowNew(false); router.replace(`/mjournal?jid=${j.id}`); void load(j.id); }}
            className={`rounded-lg border px-3.5 py-2 text-[14px] transition-colors ${
              jid === j.id ? "border-line bg-surface font-semibold text-ink shadow-sm"
                           : "border-transparent bg-raised text-muted hover:text-ink"} ${j.closed_at ? "opacity-70" : ""}`}>
            {j.name} <span className="text-[12px] text-faint">{j.symbol}</span>
            {j.closed_at && <span className="ml-1.5 rounded bg-raised px-1.5 py-0.5 text-[10.5px] font-semibold text-faint">청산</span>}
          </button>
        ))}
        <button className="btn btn-primary !py-2 text-[13.5px]" onClick={() => setShowNew(!showNew)}>＋ 새 매매일지</button>
      </div>

      {showNew && (
        <Card className="mb-4 max-w-2xl border-line-strong">
          <CardTitle>새 매매일지 — 여기서 정한 값은 이후 자동 적용됩니다</CardTitle>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1 text-[13px] text-faint">일지 이름
              <input className="input" placeholder="예: 대원제약 스윙" value={nf.name} onChange={(e) => setNf({ ...nf, name: e.target.value })} /></label>
            <label className="grid gap-1 text-[13px] text-faint">기본 종목명 (입력 시 종목 추가 가능)
              <input className="input" placeholder="예: 대원제약" value={nf.symbol} onChange={(e) => setNf({ ...nf, symbol: e.target.value })} /></label>
            <label className="grid gap-1 text-[13px] text-faint">증권사
              <input className="input" placeholder="예: NH투자증권" value={nf.broker} onChange={(e) => setNf({ ...nf, broker: e.target.value })} /></label>
            <div className="grid grid-cols-2 gap-3">
              {/* input 고유 폭이 좁은 그리드 칸을 밀어내 카드 밖으로 나가던 문제 — w-full·min-w-0 (2026-09-05) */}
              <label className="grid min-w-0 gap-1 text-[13px] text-faint">수수료율(%)
                <input className="input w-full min-w-0" value={nf.fee} onChange={(e) => setNf({ ...nf, fee: e.target.value })} /></label>
              <label className="grid min-w-0 gap-1 text-[13px] text-faint">제세금율(%, 매도)
                <input className="input w-full min-w-0" value={nf.tax} onChange={(e) => setNf({ ...nf, tax: e.target.value })} /></label>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button className="btn btn-primary" onClick={() => void createJournal()}
              disabled={!nf.name.trim() || !nf.symbol.trim()}>생성</button>
            <button className="btn" onClick={() => setShowNew(false)}>취소</button>
            {msg && <span className="text-[13.5px] text-up">{msg}</span>}
          </div>
        </Card>
      )}

      {/* 생성 폼이 열린 동안은 기존 일지 본문을 숨김 — 새 일지 내용처럼 오해 방지 (2026-09-05 지시) */}
      {showNew ? null : detail === null ? (
        <EmptyState icon="📒" title="매매일지가 없습니다"
          desc="'＋ 새 매매일지'로 종목·증권사·요율을 등록하면, 이후에는 종목·수량·단가만 입력하면 됩니다." />
      ) : (
        <>
          {/* 카드 2줄(좌 2×2) + 우측 보유 패널(2줄 높이) — 보유 종목이 많아도 가독성 유지 (2026-09-05 지시) */}
          <div className="mb-4 grid gap-3 lg:grid-cols-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:col-span-2">
            <Stat label="총 실현손익"
              value={<>{fm(detail.summary.realized)}{detail.summary.return_pct != null &&
                <span className="whitespace-nowrap text-[14px]"> ({detail.summary.return_pct >= 0 ? "+" : ""}{(detail.summary.return_pct * 100).toFixed(1)}%)</span>}</>}
              tone={detail.summary.realized > 0 ? "up" : detail.summary.realized < 0 ? "down" : "default"}
              hint="수익률 = 실현손익 ÷ 매도분 원가" />
            <Stat label="총 매도 금액" value={fm(detail.summary.sell_amount)} />
            <Stat label="총 매수 금액" value={fm(detail.summary.buy_amount)} />
            <Stat label="총 매매 비용" value={fm(detail.summary.cost)} hint="수수료 + 제세금" />
          </div>
          {(() => {
            const total = detail.holdings.reduce((a, h) => a + h.cost, 0);
            const color = (sym: string) => OV_COLORS[Math.max(detail.symbols.indexOf(sym), 0) % OV_COLORS.length];
            return (
              <div className="card flex flex-col px-4 py-3.5">
                <div className="mb-1.5 flex items-baseline justify-between gap-2">
                  <span className="text-[13px] text-faint">현재 보유 <span className="text-[11px]">(FIFO 잔여 · 취득원가 비중)</span></span>
                  {detail.holdings.length > 0 && (
                    <span className="shrink-0 text-[12px] text-faint">{detail.holdings.length}종목 · {fm(total)}</span>
                  )}
                </div>
                {detail.holdings.length === 0 ? (
                  <div className="text-[19px] font-bold">없음</div>
                ) : (
                  <div className="grid max-h-60 content-start overflow-y-auto">
                    {detail.holdings.map((h, i) => {
                      const w = total > 0 ? h.cost / total : 0;
                      return (
                        <div key={h.symbol}
                          className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-x-2 border-b border-line/50 py-1.5 text-[13px] last:border-0">
                          <span className="text-[12px] text-faint">{i + 1}</span>
                          <span className="min-w-0">
                            <b className="block truncate text-ink" title={h.symbol}>{h.symbol}</b>
                            <span className="block text-[11.5px] text-faint">{h.qty.toLocaleString()}주 @{h.avg_price.toLocaleString()}</span>
                          </span>
                          <span className="text-right">
                            <b className="table-num block text-ink">{fm(h.cost)}</b>
                            <span className="inline-flex items-center gap-1.5 text-[11.5px] text-faint">
                              <i className="inline-block h-1.5 w-12 overflow-hidden rounded-full bg-inset">
                                <i className="block h-full rounded-full" style={{ width: `${Math.max(w * 100, 2)}%`, background: color(h.symbol) }} />
                              </i>
                              {(w * 100).toFixed(0)}%
                            </span>
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })()}
          </div>

          {/* 그래프는 통계 카드 아래, 선택한 일지의 데이터만 (2026-09-05 지시) */}
          <Overview detail={detail} />

          <Card className="mb-4">
            <CardTitle right={<span className="flex items-center gap-3">
              {/* 청산 (2026-09-05 지시) — 전량 매도했거나 더 이상 거래하지 않는 일지. 기록은 남고 대시보드에서 빠진다 */}
              <button className="text-[12.5px] font-normal normal-case text-muted transition-colors hover:text-ink"
                onClick={() => void (async () => {
                  const closing = !detail.closed_at;
                  if (closing && !window.confirm(`'${detail.name}' 일지를 청산 처리할까요?\n기록은 남지만 새 기록을 받지 않고, 대시보드·총자산에서 제외됩니다.${detail.holdings.length > 0 ? `\n\n※ 보유 ${detail.holdings.length}종목이 남아 있습니다 — 실제로 전량 매도했다면 매도 기록을 먼저 넣는 것이 정확합니다.` : ""}`)) return;
                  const r = await apiFetch(`/mjournals/${detail.id}/${closing ? "close" : "reopen"}`, { method: "POST" });
                  if (r.ok) void load(detail.id);
                  else setMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `실패 (${r.status})`);
                })()}>{detail.closed_at ? "↩ 다시 열기" : "✔ 청산 처리"}</button>
              {!detail.closed_at && <button className="text-[12.5px] font-normal normal-case text-accent transition-colors hover:underline"
                onClick={() => setShowImport(!showImport)}>{showImport ? "증권사 연동 닫기" : "🔗 증권사 연동"}</button>}
              <button className="text-[12.5px] font-normal normal-case text-faint transition-colors hover:text-down"
                onClick={() => void (async () => {
                  if (!window.confirm(`'${detail.name}' 일지를 삭제할까요? 기록이 모두 삭제됩니다.`)) return;
                  const r = await apiFetch(`/mjournals/${detail.id}`, { method: "DELETE" });
                  if (r.ok) void load(null);
                })()}>🗑 일지 삭제</button>
            </span>}>
              오늘 입력 <span className="normal-case text-faint">
                · {detail.broker || "증권사 미지정"} · 수수료 {(detail.fee_rate * 100).toFixed(3)}% · 제세금 {(detail.tax_rate * 100).toFixed(2)}%
                {detail.linked_account && <> · 연결 계좌 <b className="text-ink">{detail.linked_account.label}</b></>}</span>
            </CardTitle>
            {detail.closed_at ? (
              <p className="text-[13.5px] text-muted">
                <b className="text-ink">청산된 일지입니다</b> ({detail.closed_at.slice(0, 10)}) — 기록은 보존되며 대시보드·총자산에서 제외됩니다.
                다시 거래하려면 오른쪽 위 &quot;다시 열기&quot;를 누르세요.
              </p>
            ) : (
            <div className="flex flex-wrap items-end gap-2">
              <div className="flex overflow-hidden rounded-lg border border-line-strong">
                {(["buy", "sell"] as const).map((s) => (
                  <button key={s} onClick={() => setEf({ ...ef, side: s })}
                    className={`px-4 py-2 text-[14px] font-semibold ${ef.side === s
                      ? (s === "buy" ? "bg-up text-white" : "bg-down text-white") : "bg-surface text-muted"}`}>
                    {s === "buy" ? "매수" : "매도"}
                  </button>
                ))}
              </div>
              {/* 종목 드롭다운 — 일지에 등록된 종목 + 새 종목 추가 (2026-09-05 지시) */}
              <label className="grid gap-1 text-[12.5px] text-faint">종목
                <select className="input !py-2" value={ef.symbol} onChange={(e) => setEf({ ...ef, symbol: e.target.value })}>
                  {detail.symbols.map((s) => <option key={s} value={s}>{s}</option>)}
                  <option value={NEW_SYM}>＋ 새 종목…</option>
                </select></label>
              {ef.symbol === NEW_SYM && (
                <label className="grid gap-1 text-[12.5px] text-faint">새 종목명
                  <input className="input w-36 !py-2" placeholder="예: 휴메딕스" value={ef.newSymbol}
                    onChange={(e) => setEf({ ...ef, newSymbol: e.target.value })} /></label>
              )}
              <label className="grid gap-1 text-[12.5px] text-faint">수량(주)
                <input className="input w-24 !py-2" value={ef.qty} onChange={(e) => setEf({ ...ef, qty: e.target.value })} /></label>
              <label className="grid gap-1 text-[12.5px] text-faint">단가(원)
                <input className="input w-32 !py-2" value={ef.price} onChange={(e) => setEf({ ...ef, price: e.target.value })} /></label>
              <label className="grid gap-1 text-[12.5px] text-faint">일자
                <input type="date" className="input !py-2" value={ef.date} onChange={(e) => setEf({ ...ef, date: e.target.value })} /></label>
              <label className="grid min-w-36 flex-1 gap-1 text-[12.5px] text-faint">매매 이유 (선택)
                <input className="input !py-2" placeholder="예: 코로나 테마주로 묶여 매도" value={ef.reason}
                  onChange={(e) => setEf({ ...ef, reason: e.target.value })} /></label>
              <button className="btn btn-primary !py-2.5" disabled={!(Number(ef.qty) > 0 && Number(ef.price) > 0)}
                onClick={() => void addEntry()}>등록</button>
            </div>
            )}
            {msg && <p className="mt-2 text-[13.5px] text-up">{msg}</p>}
          </Card>

          {showImport && !detail.closed_at && <BrokerImport detail={detail} accts={accts} onChanged={() => void load(detail.id)} />}

          <Card>
            <CardTitle>기록 ({detail.rows.length}건)</CardTitle>
            {detail.rows.length === 0 ? (
              <p className="text-[14px] text-faint">아직 기록이 없습니다 — 위에서 첫 매수를 등록하세요.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full whitespace-nowrap text-[13px] sm:text-[14px]">
                  <thead><tr className="border-b border-line text-left text-[12px] text-faint">
                    <th className="pb-2 font-medium">종목</th>
                    <th className="pb-2 font-medium">구분</th>
                    <th className="pb-2 font-medium">매수 일자</th>
                    <th className="pb-2 font-medium">매도 일자</th>
                    <th className="pb-2 text-right font-medium">보유(일)</th>
                    <th className="pb-2 text-right font-medium">실현손익</th>
                    <th className="pb-2 text-right font-medium">수익률</th>
                    <th className="pb-2 text-right font-medium">체결 단가</th>
                    <th className="pb-2 text-right font-medium">수량</th>
                    <th className="hidden pb-2 text-right font-medium sm:table-cell">비용</th>
                    <th className="pb-2 text-right font-medium">총 체결 금액</th>
                    <th className="pb-2 pl-3 font-medium">매매 이유</th>
                    <th className="pb-2" />
                  </tr></thead>
                  <tbody>
                    {detail.rows.map((r) => (
                      <tr key={r.id} className="border-b border-line/50 last:border-0">
                        <td className="py-2 font-semibold">{r.symbol}
                          {r.source === "broker" && <span className="ml-1 rounded bg-raised px-1 py-0.5 text-[10.5px] font-medium text-faint"
                            title={`증권사 체결 가져오기${r.code ? ` · ${r.code}` : ""}`}>증권사</span>}</td>
                        <td className={`py-2 font-bold ${r.side === "buy" ? "text-up" : "text-down"}`}>{r.side === "buy" ? "매수" : "매도"}</td>
                        <td className="py-2">{r.buy_date ?? "—"}</td>
                        <td className="py-2">{r.sell_date ?? ""}</td>
                        <td className="table-num py-2">{r.hold_days ?? ""}</td>
                        <td className={`table-num py-2 font-semibold ${r.realized == null ? "" : r.realized > 0 ? "text-up" : r.realized < 0 ? "text-down" : ""}`}>
                          {r.realized != null ? r.realized.toLocaleString() : ""}</td>
                        <td className={`table-num py-2 ${r.return_pct == null ? "" : r.return_pct > 0 ? "text-up" : "text-down"}`}>
                          {r.return_pct != null ? `${(r.return_pct * 100).toFixed(2)}%` : ""}</td>
                        <td className="table-num py-2">{r.price.toLocaleString()}</td>
                        <td className="table-num py-2">{r.qty.toLocaleString()}</td>
                        <td className="table-num hidden py-2 text-muted sm:table-cell">{r.cost != null ? r.cost.toLocaleString() : ""}</td>
                        <td className="table-num py-2">{r.amount.toLocaleString()}</td>
                        <td className="py-2 pl-3 text-muted">{r.error ? <span className="text-up">⚠ {r.error}</span> : (r.reason ?? "")}</td>
                        <td className="py-2 pl-2 text-right">
                          <button className="text-[12px] text-faint hover:text-down" title="삭제"
                            onClick={() => void (async () => {
                              if (!window.confirm("이 기록을 삭제할까요?")) return;
                              const rr = await apiFetch(`/mjournals/${detail.id}/entries/${r.id}`, { method: "DELETE" });
                              if (rr.ok) void load(detail.id);
                            })()}>✕</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </main>
  );
}
