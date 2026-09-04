"use client";

/** 주식 매매일지 — 수동 기록 (2026-09-05 지시, 스프레드시트 대체).
 *  일지 생성 시 이름·기본 종목·증권사·요율을 받고, 매일 입력은 종목·구분·수량·단가만.
 *  한 일지에 여러 종목 입력 가능(드롭다운) — 실현손익·수익률·보유기간·비용은 종목별 FIFO 로
 *  서버가 계산. 그래프(보유 비중 도넛·누적 실현손익 라인)는 통계 카드 아래 배치. */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, ensureSession } from "../../lib/api";
import { Card, CardTitle, EmptyState, PageTitle, Stat } from "../../components/ui";

type JournalMeta = { id: number; name: string; symbol: string; broker: string };
type Holding = { symbol: string; qty: number; avg_price: number; cost: number; realized: number; matched: number; return_pct: number | null };
type Row = {
  id: number; symbol: string; side: "buy" | "sell"; buy_date: string | null; sell_date: string | null;
  hold_days: number | null; realized: number | null; return_pct: number | null;
  price: number; qty: number; cost: number | null; amount: number; reason: string | null; error?: string;
};
type Detail = JournalMeta & {
  fee_rate: number; tax_rate: number; rows: Row[]; symbols: string[];
  summary: { realized: number; sell_amount: number; buy_amount: number; cost: number; return_pct: number | null };
  holdings: Holding[];
};
type OverviewItem = { id: number; name: string; symbol: string; holdings: Holding[];
  realized: number; return_pct: number | null; series: { date: string; value: number }[] };

const fm = (v: number) => `${v.toLocaleString()}원`;
const OV_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

/** 전체 현황 그래프 (2026-09-05 지시) — 통계 카드 아래 배치.
 *  좌: 종목별 보유 비중 도넛(전 일지 합산, 취득원가 기준 — 시세 미연동 명시),
 *  우: 일지별 누적 실현손익 라인. */
function Overview({ items }: { items: OverviewItem[] }) {
  const [tip, setTip] = useState<{ x: number; y: number; html: string } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const api = useRef<IChartApi | null>(null);
  // 종목별 보유 합산 (전 일지)
  const bySym = new Map<string, { qty: number; cost: number; realized: number; matched: number }>();
  for (const it of items) for (const h of it.holdings) {
    const cur = bySym.get(h.symbol) ?? { qty: 0, cost: 0, realized: 0, matched: 0 };
    bySym.set(h.symbol, { qty: cur.qty + h.qty, cost: cur.cost + h.cost,
                          realized: cur.realized + h.realized, matched: cur.matched + h.matched });
  }
  const held = [...bySym.entries()].map(([symbol, v]) => ({ symbol, ...v })).sort((a, b) => b.cost - a.cost);
  const total = held.reduce((a, h) => a + h.cost, 0);
  const symColor = (sym: string) => OV_COLORS[held.findIndex((h) => h.symbol === sym) % OV_COLORS.length];
  const withSeries = items.filter((i) => i.series.length >= 1);
  const jColor = (id: number) => OV_COLORS[items.findIndex((i) => i.id === id) % OV_COLORS.length];

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
      const pts = it.series.map((p) => ({ time: p.date, value: p.value }));
      if (pts.length === 1) {  // 점 하나로는 라인이 안 보임 — 전날 0 에서 출발
        const d0 = new Date(pts[0].time);
        d0.setDate(d0.getDate() - 1);
        pts.unshift({ time: d0.toISOString().slice(0, 10), value: 0 });
      }
      chart.addSeries(LineSeries, { color: jColor(it.id), lineWidth: 2, title: it.name }).setData(pts);
    }
    chart.timeScale().fitContent();
    return () => { try { api.current?.remove(); } catch { /* noop */ } api.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(items)]);

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
      <CardTitle>전체 현황 <span className="normal-case text-faint">· 보유 비중은 취득원가, 수익 라인은 실현손익 기준 (시세 미연동)</span></CardTitle>
      <div className="grid gap-6 lg:grid-cols-[auto_1fr]">
        <div className="flex items-center gap-4">
          {held.length > 0 && (
            <svg viewBox="0 0 128 128" className="h-32 w-32 shrink-0" role="img" aria-label="종목별 보유 비중">
              {paths.map(({ h, frac, d }, i) => (
                <path key={i} d={d} fill={symColor(h.symbol)} opacity={0.85}
                  onMouseMove={(e) => {
                    const box = (e.currentTarget.ownerSVGElement!.closest(".card") as HTMLElement).getBoundingClientRect();
                    const pct = h.matched > 0 ? h.realized / h.matched : null;  // 실현 수익률 (매도분 원가 대비)
                    setTip({ x: e.clientX - box.left + 10, y: e.clientY - box.top + 10,
                             html: `${h.symbol}\n${h.qty.toLocaleString()}주 · 원가 ${fm(h.cost)} (${(frac * 100).toFixed(1)}%)\n실현 수익률 ${pct != null ? `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(1)}%` : "— (매도 없음)"}` });
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
            <span className="font-semibold text-faint">누적 실현손익 (일지별)</span>
            {withSeries.map((it) => (
              <span key={it.id} className="inline-flex items-center gap-1.5">
                <i className="h-2 w-2 rounded-full" style={{ background: jColor(it.id) }} />
                {it.name} <b className={it.realized > 0 ? "text-up" : it.realized < 0 ? "text-down" : "text-ink"}>
                  {it.realized >= 0 ? "+" : ""}{it.realized.toLocaleString()}원
                  {it.return_pct != null && ` (${it.return_pct >= 0 ? "+" : ""}${(it.return_pct * 100).toFixed(1)}%)`}</b>
              </span>
            ))}
            {withSeries.length === 0 && <span className="text-faint">매도(실현) 기록이 생기면 추이가 그려집니다.</span>}
          </div>
          {withSeries.length > 0 && <div ref={chartRef} className="h-[190px]" />}
        </div>
      </div>
      {tip && (
        <div className="pointer-events-none absolute z-20 whitespace-pre rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12.5px] leading-relaxed shadow-lg"
          style={{ left: tip.x, top: tip.y }}>{tip.html}</div>
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
  const [overview, setOverview] = useState<OverviewItem[]>([]);

  const load = useCallback(async (selected: number | null) => {
    const r = await apiFetch("/mjournals");
    if (!r.ok) return;
    void apiFetch("/mjournals/overview").then(async (o) => {
      if (o.ok) setOverview(((await o.json()) as { items: OverviewItem[] }).items);
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
        {list.map((j) => (
          <button key={j.id} onClick={() => void load(j.id)}
            className={`rounded-lg border px-3.5 py-2 text-[14px] transition-colors ${
              jid === j.id ? "border-line bg-surface font-semibold text-ink shadow-sm"
                           : "border-transparent bg-raised text-muted hover:text-ink"}`}>
            {j.name} <span className="text-[12px] text-faint">{j.symbol}</span>
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
              <label className="grid gap-1 text-[13px] text-faint">수수료율(%)
                <input className="input" value={nf.fee} onChange={(e) => setNf({ ...nf, fee: e.target.value })} /></label>
              <label className="grid gap-1 text-[13px] text-faint">제세금율(%, 매도)
                <input className="input" value={nf.tax} onChange={(e) => setNf({ ...nf, tax: e.target.value })} /></label>
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

      {detail === null ? (
        !showNew && <EmptyState icon="📒" title="매매일지가 없습니다"
          desc="'＋ 새 매매일지'로 종목·증권사·요율을 등록하면, 이후에는 종목·수량·단가만 입력하면 됩니다." />
      ) : (
        <>
          <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(160px,1fr))]">
            <Stat label="총 실현손익"
              value={<>{fm(detail.summary.realized)}{detail.summary.return_pct != null &&
                <span className="whitespace-nowrap text-[14px]"> ({detail.summary.return_pct >= 0 ? "+" : ""}{(detail.summary.return_pct * 100).toFixed(1)}%)</span>}</>}
              tone={detail.summary.realized > 0 ? "up" : detail.summary.realized < 0 ? "down" : "default"}
              hint="수익률 = 실현손익 ÷ 매도분 원가" />
            <Stat label="총 매도 금액" value={fm(detail.summary.sell_amount)} />
            <Stat label="총 매수 금액" value={fm(detail.summary.buy_amount)} />
            <Stat label="총 매매 비용" value={fm(detail.summary.cost)} hint="수수료 + 제세금" />
            <div className="card px-4 py-3.5">
              <div className="text-[13px] text-faint">현재 보유 <span className="text-[11px]">(FIFO 잔여)</span></div>
              {detail.holdings.length === 0 ? (
                <div className="mt-1 text-[19px] font-bold">없음</div>
              ) : (
                <div className="mt-1.5 grid max-h-24 content-start gap-1 overflow-y-auto text-[13.5px]">
                  {detail.holdings.map((h) => (
                    <div key={h.symbol} className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-semibold">{h.symbol}</span>
                      <span className="shrink-0 text-muted">{h.qty.toLocaleString()}주 <span className="text-faint">@{h.avg_price.toLocaleString()}</span></span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 그래프는 통계 카드 아래 (2026-09-05 지시) */}
          <Overview items={overview} />

          <Card className="mb-4">
            <CardTitle right={
              <button className="text-[12.5px] font-normal normal-case text-faint transition-colors hover:text-down"
                onClick={() => void (async () => {
                  if (!window.confirm(`'${detail.name}' 일지를 삭제할까요? 기록이 모두 삭제됩니다.`)) return;
                  const r = await apiFetch(`/mjournals/${detail.id}`, { method: "DELETE" });
                  if (r.ok) void load(null);
                })()}>🗑 일지 삭제</button>
            }>
              오늘 입력 <span className="normal-case text-faint">
                · {detail.broker || "증권사 미지정"} · 수수료 {(detail.fee_rate * 100).toFixed(3)}% · 제세금 {(detail.tax_rate * 100).toFixed(2)}%</span>
            </CardTitle>
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
            {msg && <p className="mt-2 text-[13.5px] text-up">{msg}</p>}
          </Card>

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
                        <td className="py-2 font-semibold">{r.symbol}</td>
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
