"use client";

/** 주식 매매일지 — 수동 기록 (2026-09-05 지시, 스프레드시트 대체).
 *  일지 생성 시 이름·종목·증권사·요율을 받고, 매일 입력은 구분·수량·단가만.
 *  실현손익·수익률·보유기간·비용·합계는 서버가 FIFO 로 계산. 일지는 여러 개(이름 구분). */
import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Card, CardTitle, EmptyState, PageTitle, Stat } from "../../components/ui";

type JournalMeta = { id: number; name: string; symbol: string; broker: string };
type Row = {
  id: number; side: "buy" | "sell"; buy_date: string | null; sell_date: string | null;
  hold_days: number | null; realized: number | null; return_pct: number | null;
  price: number; qty: number; cost: number | null; amount: number; reason: string | null; error?: string;
};
type Detail = JournalMeta & {
  fee_rate: number; tax_rate: number; rows: Row[];
  summary: { realized: number; sell_amount: number; buy_amount: number; cost: number };
  holding: { qty: number; avg_price: number | null };
};

const fm = (v: number) => `${v.toLocaleString()}원`;

type OverviewItem = { id: number; name: string; symbol: string; holding_qty: number;
  holding_cost: number; avg_price: number | null; realized: number; series: { date: string; value: number }[] };
const OV_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

/** 전체 현황 (2026-09-05 지시) — 좌: 종목별 보유 비중 도넛(취득원가 기준), 우: 누적 실현손익 라인.
 *  수동 일지는 시세 미연동 — 미실현 평가손익은 표시하지 않음(원가·실현 기준임을 명시). */
function Overview({ items }: { items: OverviewItem[] }) {
  const [tip, setTip] = useState<{ x: number; y: number; html: string } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const api = useRef<IChartApi | null>(null);
  const held = items.filter((i) => i.holding_cost > 0);
  const total = held.reduce((a, i) => a + i.holding_cost, 0);
  const withSeries = items.filter((i) => i.series.length >= 1);
  const color = (id: number) => OV_COLORS[items.findIndex((i) => i.id === id) % OV_COLORS.length];

  useEffect(() => {
    if (!chartRef.current || withSeries.length === 0) return;
    api.current?.remove();
    const chart = createChart(chartRef.current, {
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
      chart.addSeries(LineSeries, { color: color(it.id), lineWidth: 2, title: it.symbol })
        .setData(pts);
    }
    chart.timeScale().fitContent();
    return () => { try { api.current?.remove(); } catch { /* noop */ } api.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(items)]);

  if (held.length === 0 && withSeries.length === 0) return null;
  const R = 52, r0 = 30, C = 64;
  let angle = -Math.PI / 2;
  const paths = held.map((it) => {
    const frac = it.holding_cost / total;
    const gap = 2 / R;
    const a0 = angle + gap / 2, a1 = angle + Math.max(frac * Math.PI * 2 - gap / 2, 0.006);
    angle += frac * Math.PI * 2;
    const pt = (a: number, rad: number) => `${C + rad * Math.cos(a)},${C + rad * Math.sin(a)}`;
    const la = a1 - a0 > Math.PI ? 1 : 0;
    return { it, frac, d: `M${pt(a0, R)} A${R},${R} 0 ${la} 1 ${pt(a1, R)} L${pt(a1, r0)} A${r0},${r0} 0 ${la} 0 ${pt(a0, r0)} Z` };
  });
  return (
    <Card className="relative mb-4">
      <CardTitle>전체 현황 <span className="normal-case text-faint">· 보유 비중은 취득원가, 수익 라인은 실현손익 기준 (시세 미연동)</span></CardTitle>
      <div className="grid gap-6 lg:grid-cols-[auto_1fr]">
        <div className="flex items-center gap-4">
          {held.length > 0 && (
            <svg viewBox="0 0 128 128" className="h-32 w-32 shrink-0" role="img" aria-label="종목별 보유 비중">
              {paths.map(({ it, frac, d }, i) => (
                <path key={i} d={d} fill={color(it.id)} opacity={0.85}
                  onMouseMove={(e) => {
                    const box = (e.currentTarget.ownerSVGElement!.parentElement!.closest(".card") as HTMLElement).getBoundingClientRect();
                    setTip({ x: e.clientX - box.left + 10, y: e.clientY - box.top + 10,
                             html: `${it.symbol}
${it.holding_qty.toLocaleString()}주 · 평단 ${fm(it.avg_price!)}
원가 ${fm(it.holding_cost)} (${(frac * 100).toFixed(1)}%)` });
                  }}
                  onMouseLeave={() => setTip(null)} />
              ))}
            </svg>
          )}
          <div className="grid content-center gap-1 text-[13px]">
            {held.map((it) => (
              <span key={it.id} className="inline-flex items-center gap-1.5 text-muted"
                title={`${it.holding_qty.toLocaleString()}주 · 원가 ${fm(it.holding_cost)}`}>
                <i className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color(it.id) }} />
                {it.symbol} <b className="text-ink">{total > 0 ? ((it.holding_cost / total) * 100).toFixed(0) : 0}%</b>
              </span>
            ))}
            {held.length === 0 && <span className="text-faint">현재 보유 없음</span>}
          </div>
        </div>
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
            <span className="font-semibold text-faint">누적 실현손익</span>
            {withSeries.map((it) => (
              <span key={it.id} className="inline-flex items-center gap-1.5">
                <i className="h-2 w-2 rounded-full" style={{ background: color(it.id) }} />
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
  const [ef, setEf] = useState({ side: "buy", qty: "", price: "", date: new Date().toISOString().slice(0, 10), reason: "" });
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
      if (d.ok) setDetail((await d.json()) as Detail);
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
    const r = await apiFetch(`/mjournals/${jid}/entries`, { method: "POST", body: JSON.stringify({
      side: ef.side, qty: Number(ef.qty), price: Number(ef.price),
      trade_date: ef.date, reason: ef.reason.trim() || undefined }) });
    if (r.ok) { setEf({ ...ef, qty: "", price: "", reason: "" }); void load(jid); }
    else setMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `등록 실패 (${r.status})`);
  }

  return (
    <main>
      <PageTitle title="주식 매매일지" sub="종목별 수동 매매 기록 — 실현손익·수익률·보유기간·비용은 자동 계산됩니다 (FIFO)" />

      <Overview items={overview} />

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
            <label className="grid gap-1 text-[13px] text-faint">종목명
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
          desc="'＋ 새 매매일지'로 종목·증권사·요율을 등록하면, 이후에는 수량과 단가만 입력하면 됩니다." />
      ) : (
        <>
          <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(160px,1fr))]">
            <Stat label="총 실현손익" value={fm(detail.summary.realized)}
              tone={detail.summary.realized > 0 ? "up" : detail.summary.realized < 0 ? "down" : "default"} />
            <Stat label="총 매도 금액" value={fm(detail.summary.sell_amount)} />
            <Stat label="총 매수 금액" value={fm(detail.summary.buy_amount)} />
            <Stat label="총 매매 비용" value={fm(detail.summary.cost)} hint="수수료 + 제세금" />
            <Stat label="현재 보유" value={detail.holding.qty > 0
              ? `${detail.holding.qty.toLocaleString()}주 · ${fm(detail.holding.avg_price!)}` : "없음"} hint="FIFO 잔여" />
          </div>

          <Card className="mb-4">
            <CardTitle right={
              <button className="text-[12.5px] font-normal normal-case text-faint transition-colors hover:text-down"
                onClick={() => void (async () => {
                  if (!window.confirm(`'${detail.name}' 일지를 삭제할까요? 기록이 모두 삭제됩니다.`)) return;
                  const r = await apiFetch(`/mjournals/${detail.id}`, { method: "DELETE" });
                  if (r.ok) void load(null);
                })()}>🗑 일지 삭제</button>
            }>
              오늘 입력 — {detail.symbol} <span className="normal-case text-faint">
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
              <label className="grid gap-1 text-[12.5px] text-faint">수량(주)
                <input className="input w-28 !py-2" value={ef.qty} onChange={(e) => setEf({ ...ef, qty: e.target.value })} /></label>
              <label className="grid gap-1 text-[12.5px] text-faint">단가(원)
                <input className="input w-36 !py-2" value={ef.price} onChange={(e) => setEf({ ...ef, price: e.target.value })} /></label>
              <label className="grid gap-1 text-[12.5px] text-faint">일자
                <input type="date" className="input !py-2" value={ef.date} onChange={(e) => setEf({ ...ef, date: e.target.value })} /></label>
              <label className="grid min-w-40 flex-1 gap-1 text-[12.5px] text-faint">매매 이유 (선택)
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
