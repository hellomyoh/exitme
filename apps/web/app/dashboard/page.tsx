"use client";

/** 자산 대시보드 — 벤토 그리드: 히어로 총자산·구성 도넛·레짐 게이지·추이·손익 캘린더 (feature-dashboard §9). */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, hasToken } from "../../lib/api";

type Dash = {
  total: number; stock: number; cash: number; other: number;
  change_amount: number; change_pct: number | null; since_inception_pct: number | null;
  manual_assets: { id: number; name: string; category: string; value: number }[];
};
type Signal = { status: string; regime?: string; e_target?: number; w_200?: number; w_lev?: number };
type CalItem = { date: string; pnl: number };

const box = { background: "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 10, padding: "14px 16px" } as const;
const won = (v: number) => `${v.toLocaleString()}원`;
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(2)}%`);
const pnlColor = (v: number) => (v > 0 ? "#e5484d" : v < 0 ? "#3b82f6" : "#c9c9d1");
const REGIME_KO: Record<string, string> = { BULL: "상승", NEUTRAL: "중립", BEAR: "하락" };
const REGIME_COLOR: Record<string, string> = { BULL: "#e5484d", NEUTRAL: "#e8b339", BEAR: "#3b82f6" };

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
      layout: { background: { color: "#111117" }, textColor: "#c9c9d1", attributionLogo: false },
      grid: { vertLines: { color: "#22222c" }, horzLines: { color: "#22222c" } },
      autoSize: true,
    });
    chartApi.current = chart;
    chart.addSeries(LineSeries, { color: "#e8b339", lineWidth: 2 }).setData(items.map((i) => ({ time: i.date, value: i.total })));
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
    ? `conic-gradient(#e8b339 0 ${(dash.stock / dash.total) * 360}deg, #4fc3f7 ${(dash.stock / dash.total) * 360}deg ${((dash.stock + dash.cash) / dash.total) * 360}deg, #ab7df8 ${((dash.stock + dash.cash) / dash.total) * 360}deg 360deg)`
    : "#22222c";

  return (
    <main style={{ padding: 16, display: "grid", gap: 12, gridTemplateColumns: "repeat(6, 1fr)", maxWidth: 1100, fontVariantNumeric: "tabular-nums" }}>
      {/* 히어로 — 총자산 (6열, 화면당 히어로 1개) */}
      <section style={{ ...box, gridColumn: "span 4" }}>
        <div style={{ opacity: 0.6, fontSize: 13 }}>총자산</div>
        <div style={{ fontSize: 40, fontWeight: 700 }}>{dash ? won(dash.total) : "—"}</div>
        {dash && (
          <div style={{ display: "flex", gap: 16, fontSize: 14 }}>
            <span style={{ color: pnlColor(dash.change_amount) }}>
              전일대비 {dash.change_amount >= 0 ? "+" : ""}{won(dash.change_amount)} ({pct(dash.change_pct)})
            </span>
            <span style={{ opacity: 0.7 }}>전체 기간 {pct(dash.since_inception_pct)}</span>
          </div>
        )}
      </section>

      {/* 레짐 게이지 */}
      <section style={{ ...box, gridColumn: "span 2" }}>
        <div style={{ opacity: 0.6, fontSize: 13 }}>RAVG v2 레짐 · 목표 노출</div>
        {signal?.status === "OK" ? (
          <>
            <div style={{ fontSize: 26, color: REGIME_COLOR[signal.regime ?? ""] }}>{REGIME_KO[signal.regime ?? ""]}</div>
            <div style={{ background: "#22222c", borderRadius: 5, height: 8, margin: "8px 0" }}>
              <div style={{ width: `${Math.min((signal.e_target ?? 0) / 1.3, 1) * 100}%`, background: REGIME_COLOR[signal.regime ?? ""], height: 8, borderRadius: 5 }} />
            </div>
            <div style={{ fontSize: 13, opacity: 0.8 }}>E {pct(signal.e_target)} · K200 {pct(signal.w_200)} · 레버리지 {pct(signal.w_lev)}</div>
          </>
        ) : <div style={{ opacity: 0.6, marginTop: 8 }}>{signal?.status ?? "—"} (시딩·배치 후 표시)</div>}
      </section>

      {/* 자산 구성 도넛 */}
      <section style={{ ...box, gridColumn: "span 2", display: "flex", gap: 14, alignItems: "center" }}>
        <div style={{ width: 92, height: 92, borderRadius: "50%", background: donut, position: "relative" }}>
          <div style={{ position: "absolute", inset: 14, borderRadius: "50%", background: "#1a1a22" }} />
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.9 }}>
          <div><span style={{ color: "#e8b339" }}>●</span> 주식 {dash ? won(dash.stock) : "—"}</div>
          <div><span style={{ color: "#4fc3f7" }}>●</span> 현금 {dash ? won(dash.cash) : "—"}</div>
          <div><span style={{ color: "#ab7df8" }}>●</span> 기타 {dash ? won(dash.other) : "—"}</div>
        </div>
      </section>

      {/* 자산 추이 */}
      <section style={{ ...box, gridColumn: "span 4" }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ opacity: 0.6, fontSize: 13 }}>자산 추이</span>
          <span>
            {["1M", "3M", "6M", "1Y", "ALL"].map((r) => (
              <button key={r} onClick={() => { setRange(r); void loadTrend(r); }}
                style={{ background: r === range ? "#2d2d3a" : "transparent", color: "#c9c9d1", border: "none", cursor: "pointer", padding: "2px 6px", borderRadius: 4 }}>
                {r}
              </button>
            ))}
          </span>
        </div>
        <div ref={trendRef} style={{ height: 200 }} />
        <div style={{ opacity: 0.4, fontSize: 11 }}>일별 스냅샷 기준 (장 마감 후 적재) · 지연 시세</div>
      </section>

      {/* 손익 캘린더 */}
      <section style={{ ...box, gridColumn: "span 2" }}>
        <div style={{ opacity: 0.6, fontSize: 13, marginBottom: 8 }}>이번 달 일간 손익</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 3 }}>
          {calendar.map((c) => (
            <div key={c.date} title={`${c.date}: ${won(c.pnl)}`}
              style={{ aspectRatio: "1", borderRadius: 3, background: c.pnl > 0 ? "#e5484d" : c.pnl < 0 ? "#3b82f6" : "#22222c", opacity: Math.min(Math.abs(c.pnl) / 500000 + 0.25, 1) }} />
          ))}
          {calendar.length === 0 && <span style={{ gridColumn: "span 7", opacity: 0.5, fontSize: 12 }}>스냅샷 누적 후 표시됩니다</span>}
        </div>
      </section>

      {/* 기타 자산 등록 */}
      <section style={{ ...box, gridColumn: "span 6" }}>
        <div style={{ opacity: 0.6, fontSize: 13, marginBottom: 8 }}>기타 자산 (수동 등록)</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {dash?.manual_assets.map((m) => (
            <span key={m.id} style={{ background: "#22222c", borderRadius: 6, padding: "4px 10px", fontSize: 13 }}>
              {m.name} ({m.category}) {won(m.value)}
              <button style={{ background: "none", border: "none", color: "#f2617a", cursor: "pointer" }}
                onClick={() => void apiFetch(`/manual-assets/${m.id}`, { method: "DELETE" }).then(() => load())}>×</button>
            </span>
          ))}
          <input placeholder="이름" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            style={{ background: "#22222c", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "4px 8px", width: 110 }} />
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
            style={{ background: "#22222c", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "4px 8px" }}>
            {["예금", "채권", "펀드", "금", "코인", "부동산", "기타"].map((c) => <option key={c}>{c}</option>)}
          </select>
          <input placeholder="평가액(원)" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })}
            style={{ background: "#22222c", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "4px 8px", width: 130 }} />
          <button onClick={() => void addManual()}
            style={{ background: "#2d2d3a", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "4px 12px", cursor: "pointer" }}>추가</button>
        </div>
      </section>
    </main>
  );
}
