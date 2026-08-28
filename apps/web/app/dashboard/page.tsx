"use client";

/** 자산 대시보드 — 벤토: 히어로 총자산·구성·레짐 게이지·추이·손익 캘린더 (feature-dashboard §9). */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createChart, IChartApi, AreaSeries } from "lightweight-charts";
import { apiFetch, hasToken } from "../../lib/api";
import { Badge, Card, CardTitle, fmtPct, fmtWon, GaugeBar, PageTitle, pnlTone } from "../../components/ui";

type Dash = {
  total: number; stock: number; cash: number; other: number;
  change_amount: number; change_pct: number | null; since_inception_pct: number | null;
  manual_assets: { id: number; name: string; category: string; value: number }[];
};
type Signal = { status: string; regime?: string; e_target?: number; w_200?: number; w_lev?: number };
type CalItem = { date: string; pnl: number };

const REGIME_KO: Record<string, string> = { BULL: "상승장", NEUTRAL: "중립장", BEAR: "하락장" };
const REGIME_COLOR: Record<string, string> = { BULL: "var(--color-up)", NEUTRAL: "var(--color-accent)", BEAR: "var(--color-down)" };
const toneCls = { up: "text-up", down: "text-down", default: "text-muted" };

export default function DashboardPage() {
  const router = useRouter();
  const [dash, setDash] = useState<Dash | null>(null);
  const [signal, setSignal] = useState<Signal | null>(null);
  const [calendar, setCalendar] = useState<CalItem[]>([]);
  const [range, setRange] = useState("3M");
  const [form, setForm] = useState({ name: "", category: "예금", value: "" });
  const trendRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);

  const load = useCallback(async () => {
    const d = await apiFetch("/dashboard");
    if (d.ok) setDash((await d.json()) as Dash);
    const s = await apiFetch("/signals/daily");
    if (s.ok) setSignal((await s.json()) as Signal);
    const month = new Date().toISOString().slice(0, 7);
    const c = await apiFetch(`/portfolio/calendar?month=${month}`);
    if (c.ok) setCalendar(((await c.json()) as { items: CalItem[] }).items);
  }, []);

  const disposeChart = useCallback(() => {
    try { chartApi.current?.remove(); } catch { /* already disposed */ }
    chartApi.current = null;
  }, []);

  const loadTrend = useCallback(async (r: string) => {
    const res = await apiFetch(`/portfolio/trend?range_=${r}`);
    if (!res.ok || !trendRef.current) return;
    const items = ((await res.json()) as { items: { date: string; total: number }[] }).items;
    disposeChart();
    if (items.length < 2) return;
    const chart = createChart(trendRef.current, {
      layout: { background: { color: "transparent" }, textColor: "#71717e", attributionLogo: false, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.06)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      autoSize: true,
    });
    chartApi.current = chart;
    chart.addSeries(AreaSeries, {
      lineColor: "#f0b429", lineWidth: 2,
      topColor: "rgba(240,180,41,0.25)", bottomColor: "rgba(240,180,41,0.0)",
      priceLineVisible: false,
    }).setData(items.map((i) => ({ time: i.date, value: i.total })));
    chart.timeScale().fitContent();
  }, [disposeChart]);

  useEffect(() => {
    if (!hasToken()) { router.push("/login"); return; }
    void load().then(() => loadTrend(range));
    return () => disposeChart();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addManual() {
    if (!form.name || !form.value) return;
    await apiFetch("/manual-assets", { method: "POST", body: JSON.stringify({ name: form.name, category: form.category, value: Number(form.value) }) });
    setForm({ name: "", category: form.category, value: "" });
    void load();
  }

  const donut = dash && dash.total > 0
    ? `conic-gradient(var(--color-accent) 0 ${(dash.stock / dash.total) * 360}deg, var(--color-down) ${(dash.stock / dash.total) * 360}deg ${((dash.stock + dash.cash) / dash.total) * 360}deg, #8b7cf6 ${((dash.stock + dash.cash) / dash.total) * 360}deg 360deg)`
    : "var(--color-raised)";
  const ct = pnlTone(dash?.change_amount ?? 0);

  return (
    <main>
      <PageTitle title="대시보드" sub="총자산과 전략 상태를 한 화면에서 — 일별 스냅샷 기준, 지연 시세" />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-6">
        {/* 히어로 — 총자산 */}
        <Card className="md:col-span-4">
          <CardTitle>총자산</CardTitle>
          <div className="text-4xl font-extrabold tracking-tight">{dash ? fmtWon(dash.total) : "—"}</div>
          {dash && (
            <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-[13.5px]">
              <span className={`font-semibold ${toneCls[ct]}`}>
                {dash.change_amount >= 0 ? "▲" : "▼"} 전일대비 {fmtWon(Math.abs(dash.change_amount))} ({fmtPct(dash.change_pct, 2)})
              </span>
              <span className="text-faint">전체 기간 {fmtPct(dash.since_inception_pct, 2)}</span>
            </div>
          )}
        </Card>

        {/* 레짐 게이지 */}
        <Card className="md:col-span-2">
          <CardTitle>RAVG v2 레짐</CardTitle>
          {signal?.status === "OK" ? (
            <>
              <div className="mb-3 flex items-center gap-2">
                <span className="text-xl font-extrabold" style={{ color: REGIME_COLOR[signal.regime ?? ""] }}>
                  {REGIME_KO[signal.regime ?? ""]}
                </span>
                <Badge tone="accent">E {fmtPct(signal.e_target)}</Badge>
              </div>
              <GaugeBar ratio={(signal.e_target ?? 0) / 1.3} color={REGIME_COLOR[signal.regime ?? ""]} />
              <div className="mt-2 text-xs text-faint">K200 {fmtPct(signal.w_200)} · 레버리지 {fmtPct(signal.w_lev)}</div>
            </>
          ) : <p className="text-[13px] text-faint">{signal?.status ?? "—"} — 시딩·배치 후 표시됩니다</p>}
        </Card>

        {/* 자산 추이 */}
        <Card className="md:col-span-4">
          <CardTitle right={
            <span className="flex gap-1">
              {["1M", "3M", "6M", "1Y", "ALL"].map((r) => (
                <button key={r} onClick={() => { setRange(r); void loadTrend(r); }}
                  className={`rounded-md px-2 py-0.5 text-xs font-medium transition-colors ${r === range ? "bg-raised text-ink" : "text-faint hover:text-ink"}`}>
                  {r}
                </button>
              ))}
            </span>
          }>자산 추이</CardTitle>
          <div ref={trendRef} className="h-52" />
        </Card>

        {/* 자산 구성 */}
        <Card className="md:col-span-2">
          <CardTitle>자산 구성</CardTitle>
          <div className="flex items-center gap-5">
            <div className="relative h-24 w-24 shrink-0 rounded-full" style={{ background: donut }}>
              <div className="absolute inset-3.5 rounded-full bg-surface" />
            </div>
            <div className="grid gap-1.5 text-[13px]">
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full bg-accent" />주식 <b>{dash ? fmtWon(dash.stock) : "—"}</b></span>
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full bg-down" />현금 <b>{dash ? fmtWon(dash.cash) : "—"}</b></span>
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: "#8b7cf6" }} />기타 <b>{dash ? fmtWon(dash.other) : "—"}</b></span>
            </div>
          </div>
        </Card>

        {/* 손익 캘린더 */}
        <Card className="md:col-span-3">
          <CardTitle>이번 달 일간 손익</CardTitle>
          {calendar.length > 0 ? (
            <div className="grid grid-cols-10 gap-1.5">
              {calendar.map((c) => (
                <div key={c.date} title={`${c.date} · ${fmtWon(c.pnl)}`}
                  className="aspect-square rounded-[5px]"
                  style={{
                    background: c.pnl > 0 ? "var(--color-up)" : c.pnl < 0 ? "var(--color-down)" : "var(--color-raised)",
                    opacity: c.pnl === 0 ? 0.6 : Math.min(Math.abs(c.pnl) / 500000 + 0.3, 1),
                  }} />
              ))}
            </div>
          ) : <p className="text-[13px] text-faint">일별 스냅샷이 쌓이면 표시됩니다.</p>}
        </Card>

        {/* 기타 자산 */}
        <Card className="md:col-span-3">
          <CardTitle>기타 자산 (수동 등록)</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            {dash?.manual_assets.map((m) => (
              <span key={m.id} className="inline-flex items-center gap-1.5 rounded-lg bg-raised px-3 py-1.5 text-[13px]">
                <span className="text-faint">{m.category}</span> {m.name} <b>{fmtWon(m.value)}</b>
                <button className="ml-1 text-faint transition-colors hover:text-up"
                  onClick={() => void apiFetch(`/manual-assets/${m.id}`, { method: "DELETE" }).then(() => load())}>✕</button>
              </span>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <input className="input w-28" placeholder="이름" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <select className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {["예금", "채권", "펀드", "금", "코인", "부동산", "기타"].map((c) => <option key={c}>{c}</option>)}
            </select>
            <input className="input w-32" placeholder="평가액(원)" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
            <button className="btn" onClick={() => void addManual()}>추가</button>
          </div>
        </Card>
      </div>
    </main>
  );
}
