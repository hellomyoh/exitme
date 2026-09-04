"use client";

/** 자산 대시보드 — 벤토: 히어로 총자산·구성·레짐 게이지·추이·손익 캘린더 (feature-dashboard §9). */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createChart, IChartApi, AreaSeries, LineSeries } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { MarketFlag } from "../../components/flags";
import { Spark } from "../../components/spark";
import { Badge, Card, CardTitle, fmtPct, fmtWon, GaugeBar, PageTitle, pnlTone } from "../../components/ui";

type Breakdown = { value: number; cost: number; pnl: number; pnl_pct: number | null };
type PortPosition = { code: string; name: string; qty: number; value: number };
type PortRow = { trend?: number[];  id: number; name: string; market: string; equity: number; stock_value: number; cash: number; pnl: number; pnl_pct: number | null; color?: string | null; positions?: PortPosition[] };

/** 계좌별 도넛 (2026-09-05 지시) — 계좌마다 도넛 하나, 조각 = 보유 종목(+현금).
 *  같은 종목은 모든 도넛에서 같은 색(색은 엔티티를 따른다). hover 에 종목/수량/평가액. */
const INST_COLORS: Record<string, string> = {
  "102110": "#2a78d6",  // TIGER 200 — blue
  "069500": "#eb6834",  // KODEX 200 — orange
  "122630": "#1baf7a",  // KODEX 레버리지 — aqua
  QQQ: "#2a78d6", QLD: "#1baf7a", TQQQ: "#eda100",
};
const CASH_COLOR = "#a8a29e";
const FALLBACK_COLOR = "#4a3aa7";

function AccountDonut({ row, fmt }: { row: PortRow; fmt: (v: number) => string }) {
  const [tip, setTip] = useState<{ x: number; y: number; html: string } | null>(null);
  type Slice = { label: string; sub: string; value: number; color: string };
  const slices: Slice[] = (row.positions ?? []).map((p) => ({
    label: p.name, sub: `${p.qty.toLocaleString()}주 · ${fmt(p.value)}`,
    value: p.value, color: INST_COLORS[p.code] ?? FALLBACK_COLOR,
  }));
  if (row.cash > 0) slices.push({ label: "현금", sub: fmt(row.cash), value: row.cash, color: CASH_COLOR });
  const total = slices.reduce((a, s) => a + s.value, 0);
  if (total <= 0) return null;
  // SVG 아크 — 12시 시작 시계 방향, 조각 사이 2px 간극(스페이서 규칙)
  const R = 52, r0 = 30, C = 64;
  const gapRad = 2 / R;
  let angle = -Math.PI / 2;
  const paths = slices.map((s) => {
    const frac = s.value / total;
    const a0 = angle + gapRad / 2;
    const a1 = angle + Math.max(frac * Math.PI * 2 - gapRad / 2, 0.006);
    angle += frac * Math.PI * 2;
    const p = (a: number, rad: number) => `${C + rad * Math.cos(a)},${C + rad * Math.sin(a)}`;
    const largeArc = a1 - a0 > Math.PI ? 1 : 0;
    return { s, frac, d: `M${p(a0, R)} A${R},${R} 0 ${largeArc} 1 ${p(a1, R)} L${p(a1, r0)} A${r0},${r0} 0 ${largeArc} 0 ${p(a0, r0)} Z` };
  });
  return (
    <div className="relative flex flex-col items-center gap-1.5">
      {/* 계좌명 클릭 → 해당 실전매매로 이동 (2026-09-05 지시) */}
      <Link href={`/portfolio?${row.market === "US" ? "market=US&" : ""}pid=${row.id}`}
        className="flex max-w-full items-center gap-1.5 text-[13.5px] font-semibold underline-offset-2 hover:text-accent hover:underline">
        <MarketFlag market={row.market} />
        {row.color && <i className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: row.color }} />}
        <span className="truncate">{row.name}</span>
      </Link>
      <svg viewBox="0 0 128 128" className="h-28 w-28" role="img" aria-label={`${row.name} 종목 구성`}>
        {paths.map(({ s, frac, d }, i) => (
          <path key={i} d={d} fill={s.color} opacity={0.85}
            onMouseMove={(e) => {
              const box = (e.currentTarget.ownerSVGElement!.parentElement as HTMLElement).getBoundingClientRect();
              setTip({ x: e.clientX - box.left + 10, y: e.clientY - box.top + 10,
                       html: `${s.label}\n${s.sub} (${(frac * 100).toFixed(1)}%)` });
            }}
            onMouseLeave={() => setTip(null)} />
        ))}
      </svg>
      <div className="text-[14.5px] font-bold tabular-nums">{fmt(row.equity)}</div>
      {row.pnl !== 0 && (
        <div className={`-mt-1 text-[12.5px] font-semibold ${row.pnl > 0 ? "text-up" : "text-down"}`}>
          {row.pnl >= 0 ? "+" : ""}{fmt(row.pnl)}{row.pnl_pct !== null && ` (${(row.pnl_pct * 100).toFixed(2)}%)`}
        </div>
      )}
      {tip && (
        <div className="pointer-events-none absolute z-20 whitespace-pre rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12.5px] leading-relaxed shadow-lg"
          style={{ left: tip.x, top: tip.y }}>{tip.html}</div>
      )}
    </div>
  );
}
type Dash = { portfolios?: PortRow[]; total_trend?: number[]; kr_trend?: number[]; us_trend?: number[]; 
  total: number; stock: number; cash: number; other: number;
  change_amount: number; change_pct: number | null; since_inception_pct: number | null;
  kr_stock: Breakdown; us_stock: Breakdown;  // us_stock 값 단위: 센트
  manual_assets: { id: number; name: string; category: string; value: number }[];
};
type TrendSeries = { portfolio_id: number; name: string; market: string; currency: string;
  points: { date: string; equity: number }[] };
type Signal = { status: string; regime?: string; e_target?: number; w_200?: number; w_lev?: number };
type CalItem = { date: string; pnl: number };

const REGIME_KO: Record<string, string> = { BULL: "상승장", NEUTRAL: "중립장", BEAR: "하락장" };
const REGIME_COLOR: Record<string, string> = { BULL: "var(--color-up)", NEUTRAL: "var(--color-accent)", BEAR: "var(--color-down)" };
const toneCls = { up: "text-up", down: "text-down", default: "text-muted" };

export default function DashboardPage() {
  const router = useRouter();
  const [loadError, setLoadError] = useState("");
  const [dash, setDash] = useState<Dash | null>(null);
  const [signal, setSignal] = useState<Signal | null>(null);
  const [calendar, setCalendar] = useState<CalItem[]>([]);
  const [range, setRange] = useState("3M");
  const [form, setForm] = useState({ name: "", category: "예금", value: "" });
  const [trendLegend, setTrendLegend] = useState<{ name: string; color: string }[]>([]);
  const trendRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);

  const load = useCallback(async () => {
    const d = await apiFetch("/dashboard");
    if (d.ok) { setLoadError(""); setDash((await d.json()) as Dash); }
    else setLoadError(`대시보드 데이터를 불러오지 못했습니다 (HTTP ${d.status}) — 서버 로그·마이그레이션 상태를 확인하세요.`);
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

  // 포트별 다선 색 — 총자산(주황 면적) 외 KRW 포트 라인 (feature-dashboard §8, ADR-008)
  const SERIES_COLORS = ["#2563eb", "#059669", "#7c3aed", "#db2777", "#0891b2", "#ca8a04"];

  const loadTrend = useCallback(async (r: string) => {
    const res = await apiFetch(`/portfolio/trend?range_=${r}`);
    if (!res.ok || !trendRef.current) return;
    const body = (await res.json()) as { items: { date: string; total: number }[]; series?: TrendSeries[] };
    const items = body.items;
    disposeChart();
    if (items.length < 2) return;
    const korUnit = (v: number) => {
      const a = Math.abs(v);
      if (a >= 1e8) return `${(v / 1e8).toFixed(a >= 1e9 ? 0 : 1)}억`;
      if (a >= 1e4) return `${Math.round(v / 1e4).toLocaleString()}만`;
      return `${Math.round(v).toLocaleString()}`;
    };
    const chart = createChart(trendRef.current, {
      localization: { priceFormatter: korUnit },  // 축 금액 억/만 자동 단위 (2026-09-02 지시)
      layout: { background: { color: "transparent" }, textColor: "#858c9b", attributionLogo: false, fontSize: 12 },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(18,24,40,0.07)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      autoSize: true,
    });
    chartApi.current = chart;
    chart.addSeries(AreaSeries, {
      lineColor: "#f97316", lineWidth: 2,
      topColor: "rgba(180,83,9,0.16)", bottomColor: "rgba(180,83,9,0.0)",
      priceLineVisible: false,
    }).setData(items.map((i) => ({ time: i.date, value: i.total })));
    // 실전매매 포트별 라인 — KRW 만 (US 는 센트 단위라 환율 도입 전 제외, ASSUMPTIONS 2026-09-02)
    const legend: { name: string; color: string }[] = [{ name: "총자산", color: "#f97316" }];
    (body.series ?? []).filter((sr) => sr.currency === "KRW" && sr.points.length >= 2)
      .forEach((sr, i) => {
        const color = SERIES_COLORS[i % SERIES_COLORS.length];
        chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
          .setData(sr.points.map((pt) => ({ time: pt.date, value: pt.equity })));
        legend.push({ name: sr.name, color });
      });
    setTrendLegend(legend.length > 1 ? legend : []);
    chart.timeScale().fitContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disposeChart]);

  useEffect(() => {
    void ensureSession().then((ok) => {
      if (!ok) { router.push("/login"); return; }
      void load().then(() => loadTrend(range));
    });
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
    ? `conic-gradient(var(--color-accent) 0 ${(dash.stock / dash.total) * 360}deg, var(--color-down) ${(dash.stock / dash.total) * 360}deg ${((dash.stock + dash.cash) / dash.total) * 360}deg, #7c3aed ${((dash.stock + dash.cash) / dash.total) * 360}deg 360deg)`
    : "var(--color-raised)";
  const ct = pnlTone(dash?.change_amount ?? 0);

  return (
    <main>
      <PageTitle title="대시보드" sub="총자산과 전략 상태를 한 화면에서 — 일별 스냅샷 기준, 지연 시세" />
      {loadError && <div className="mb-4 rounded-xl border border-down/40 bg-down/5 px-4 py-3 text-[14px] font-semibold text-down">⚠️ {loadError}</div>}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-6">
        {/* 1열 KPI 4카드 — 숫자+스파크라인, 자산 내용과의 중복 제거 (2026-09-05 지시) */}
        <Card className="px-4 py-3.5 md:col-span-2">
          <div className="text-[13px] text-faint">총자산 (KRW · 미국 자산 별도)</div>
          <div className="mt-1 text-[24px] font-extrabold tracking-tight">{dash ? fmtWon(dash.total) : "—"}</div>
          {dash && (
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-[12.5px]">
              <span className={`font-semibold ${toneCls[ct]}`}>
                {dash.change_amount >= 0 ? "▲" : "▼"} 전일 {fmtWon(Math.abs(dash.change_amount))} ({fmtPct(dash.change_pct, 2)})
              </span>
              <span className="text-faint">전체 {fmtPct(dash.since_inception_pct, 2)}</span>
            </div>
          )}
          <Spark data={dash?.total_trend} />
        </Card>
        <Card className="px-4 py-3.5 md:col-span-1">
          <div className="flex items-center gap-1.5 text-[13px] text-faint"><MarketFlag market="KR" /> 한국 주식</div>
          <div className="mt-1 text-[20px] font-bold">{dash ? fmtWon(dash.kr_stock.value) : "—"}</div>
          {dash && dash.kr_stock.cost > 0 && (
            <div className={`text-[12.5px] font-semibold ${toneCls[pnlTone(dash.kr_stock.pnl)]}`}>
              {dash.kr_stock.pnl >= 0 ? "+" : ""}{fmtWon(dash.kr_stock.pnl)}
              {dash.kr_stock.pnl_pct != null && ` (${fmtPct(dash.kr_stock.pnl_pct, 2)})`}
            </div>
          )}
          <Spark data={dash?.kr_trend} color="#2a78d6" />
        </Card>
        <Card className="px-4 py-3.5 md:col-span-1">
          <div className="flex items-center gap-1.5 text-[13px] text-faint"><MarketFlag market="US" /> 미국 주식 ($)</div>
          <div className="mt-1 text-[20px] font-bold">
            {dash ? `$${(dash.us_stock.value / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}` : "—"}
          </div>
          {dash && dash.us_stock.cost > 0 && (
            <div className={`text-[12.5px] font-semibold ${toneCls[pnlTone(dash.us_stock.pnl)]}`}>
              {dash.us_stock.pnl >= 0 ? "+" : ""}${(dash.us_stock.pnl / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}
              {dash.us_stock.pnl_pct != null && ` (${fmtPct(dash.us_stock.pnl_pct, 2)})`}
            </div>
          )}
          <Spark data={dash?.us_trend} color="#1baf7a" />
        </Card>
        <Card className="px-4 py-3.5 md:col-span-2">
          <CardTitle>RAVG v2.5 레짐</CardTitle>
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

        {/* 계좌별 현황 — 도넛 → 순위 테이블 (2026-09-05 지시, Zenith 'Top Selling' 스타일) */}
        {(dash?.portfolios?.length ?? 0) > 0 && (
          <Card className="md:col-span-6">
            <CardTitle>계좌별 현황 <span className="normal-case text-faint">· 진행 중 실전매매 — 이름 클릭 시 해당 실전매매로</span></CardTitle>
            <div className="overflow-x-auto">
              <table className="w-full whitespace-nowrap text-[14px]">
                <thead><tr className="border-b border-line text-left text-[12px] text-faint">
                  <th className="pb-2 pr-2 font-medium">#</th>
                  <th className="pb-2 font-medium">계좌</th>
                  <th className="pb-2 text-right font-medium">평가액</th>
                  <th className="pb-2 text-right font-medium">평가손익</th>
                  <th className="pb-2 pl-6 font-medium">추세</th>
                </tr></thead>
                <tbody>
                  {(dash!.portfolios ?? []).filter((p) => p.equity > 0)
                    .sort((a, b) => b.equity - a.equity)
                    .map((p, i) => {
                      const money = (v: number) => p.market === "US"
                        ? `$${(v / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}` : `${v.toLocaleString()}원`;
                      const posSummary = (p.positions ?? []).map((x) => `${x.name} ${x.qty.toLocaleString()}주`).join(" · ");
                      return (
                        <tr key={p.id} className="border-b border-line/50 last:border-0">
                          <td className="py-2.5 pr-2 text-faint">{i + 1}</td>
                          <td className="py-2.5">
                            <Link href={`/portfolio?${p.market === "US" ? "market=US&" : ""}pid=${p.id}`}
                              className="group inline-flex items-center gap-1.5">
                              <MarketFlag market={p.market} />
                              {p.color && <i className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: p.color }} />}
                              <span className="font-semibold underline-offset-2 group-hover:text-accent group-hover:underline">{p.name}</span>
                            </Link>
                            <div className="text-[12px] text-faint">{posSummary || "현금 대기"}</div>
                          </td>
                          <td className="table-num py-2.5 font-bold">{money(p.equity)}</td>
                          <td className={`table-num py-2.5 font-semibold ${p.pnl > 0 ? "text-up" : p.pnl < 0 ? "text-down" : "text-faint"}`}>
                            {p.pnl !== 0 ? `${p.pnl >= 0 ? "+" : ""}${money(p.pnl)}` : "—"}
                            {p.pnl_pct !== null && p.pnl !== 0 && ` (${(p.pnl_pct * 100).toFixed(2)}%)`}
                          </td>
                          <td className="py-1 pl-6"><div className="w-28"><Spark data={p.trend} className="h-7 w-full" /></div></td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </Card>
        )}

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
          {trendLegend.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted">
              {/* 이름은 중복 가능(예: '내 계좌' 2개) — 위치 기반 키 (2026-09-05 중복 키 오류) */}
              {trendLegend.map((l, i) => (
                <span key={i} className="inline-flex items-center gap-1.5">
                  <i className="inline-block h-2 w-2 rounded-full" style={{ background: l.color }} />{l.name}
                </span>
              ))}
            </div>
          )}
        </Card>

        {/* 자산 구성 */}
        <Card className="md:col-span-2">
          <CardTitle>자산 구성</CardTitle>
          <div className="flex items-center gap-5">
            <div className="relative h-24 w-24 shrink-0 rounded-full" style={{ background: donut }}>
              <div className="absolute inset-3.5 rounded-full bg-surface" />
            </div>
            <div className="grid gap-2 text-[14.5px]">
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full bg-accent" />주식 <b>{dash ? fmtWon(dash.stock) : "—"}</b></span>
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full bg-down" />현금 <b>{dash ? fmtWon(dash.cash) : "—"}</b></span>
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: "#7c3aed" }} />기타 <b>{dash ? fmtWon(dash.other) : "—"}</b></span>
            </div>
          </div>
        </Card>

        {/* 손익 캘린더 */}
        <Card className="md:col-span-3">
          <CardTitle>이번 달 일간 손익</CardTitle>
          {calendar.length > 0 ? (
            <div className="grid grid-cols-10 gap-1.5">
              {calendar.map((c) => (
                // 기본 title 툴팁은 ~1초 정지해야 뜨고 환경에 따라 미표시 — 앱 공통 group-hover 패턴으로 즉시 표시 (2026-09-02)
                <div key={c.date} className="group relative">
                  <div role="img" aria-label={`${c.date} ${fmtWon(c.pnl)}`}
                    className="aspect-square rounded-[5px]"
                    style={{
                      background: c.pnl > 0 ? "var(--color-up)" : c.pnl < 0 ? "var(--color-down)" : "var(--color-raised)",
                      opacity: c.pnl === 0 ? 0.6 : Math.min(Math.abs(c.pnl) / 500000 + 0.3, 1),
                    }} />
                  <span className="pointer-events-none invisible absolute bottom-full left-1/2 z-30 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[12.5px] tabular-nums text-ink opacity-0 shadow-lg transition-opacity duration-150 group-hover:visible group-hover:opacity-100">
                    {c.date} · <b className={c.pnl > 0 ? "text-up" : c.pnl < 0 ? "text-down" : ""}>{c.pnl > 0 ? "+" : ""}{fmtWon(c.pnl)}</b>
                  </span>
                </div>
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
