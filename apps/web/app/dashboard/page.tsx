"use client";

/** 자산 대시보드 — 벤토: 히어로 총자산·구성·레짐 게이지·추이·손익 캘린더 (feature-dashboard §9). */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createChart, AreaSeries, LineSeries, LineStyle,
  type IChartApi, type ISeriesApi, type LineWidth } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { MarketFlag } from "../../components/flags";
import { Spark } from "../../components/spark";
import { Badge, Card, CardTitle, fmtPct, fmtWon, GaugeBar, PageTitle, pnlTone, Stat, Tip } from "../../components/ui";

// 시장 카드: 누적(pnl, 보유 원가 대비)과 오늘(day_change, 전일 종가 평가액 대비) — 2026-09-10 지시
type Breakdown = { value: number; cost: number; pnl: number; pnl_pct: number | null;
  day_change?: number | null; day_change_pct?: number | null; day_change_asof?: string | null };
type PortPosition = { code: string; name: string; qty: number; value: number };
// 오늘 손익 (2026-09-10 지시): 누적(pnl)은 보유 원가 대비, 오늘(day_change)은 **전일 종가 평가액 대비**.
// day_missing = 전일 종가가 없어 오늘 손익에서 뺀 종목(오늘 신규 매수·시세 미확보)
type PortRow = { trend?: number[]; day_change?: number; day_change_pct?: number | null; day_missing?: string[]; price_source?: string; day_change_asof?: string | null;  id: number; name: string; market: string; equity: number; stock_value: number; cash: number; pnl: number; pnl_pct: number | null; color?: string | null; positions?: PortPosition[] };

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
  trading_total?: number; journal?: number; journals?: JournalAsset[];   // 주식 거래 자산 / 매매일지 자산 분리 (2026-09-05)
  change_amount: number; change_pct: number | null; since_inception_pct: number | null;
  change_asof?: string | null;   // 오늘 손익의 기준일 — null=오늘, 날짜=그날 종가 기준 (주말·휴장·장 시작 전·적재 지연, 2026-09-12)
  since_inception_amount?: number | null;   // 누적 금액 (최초 스냅샷 대비, 입출금 제외) — 2026-09-10
  live_at?: string | null;   // 10초 폴링 시세로 평가한 시각 (없으면 종가 기준, 2026-09-10)
  kr_stock: Breakdown; us_stock: Breakdown;  // us_stock 값 단위: 센트
  manual_assets: { id: number; name: string; category: string; value: number }[];
};
type JournalAsset = { excluded?: { symbol: string; code: string | null; value: number }[]; id: number; name: string; symbol: string; cost: number; realized: number; return_pct: number | null;
  value?: number; priced?: boolean; unrealized?: number | null; unrealized_pct?: number | null;   // 현재가 평가 (2026-09-06)
  day_change?: number | null; day_change_pct?: number | null; day_missing?: string[]; day_change_asof?: string | null;  // 오늘 손익 (2026-09-10)·기준일 (2026-09-12)
  holdings: { symbol: string; qty: number; cost: number; price?: number | null; eval?: number | null }[]; entries: number; counted: boolean; note: string | null };
type TrendSeries = { portfolio_id: number | null; journal_id?: number; name: string; market: string; currency: string;
  kind?: string;   // "portfolio" | "journal" (2026-09-10) — 매매일지는 0028 부터 일지별 한 줄
  approx?: boolean;   // 소급 재계산분 포함 (기록·종가로 되살린 값, 2026-09-12)
  points: { date: string; equity: number }[] };
type TrendKind = "total" | "port" | "journal";
type TrendHandle = { name: string; color: string; kind: TrendKind;
  api: ISeriesApi<"Area"> | ISeriesApi<"Line"> };
type TrendTip = { x: number; flip: boolean; date: string;
  rows: { name: string; color: string; value: number }[] };
type Signal = { status: string; regime?: string; e_target?: number; w_200?: number; w_lev?: number };
type CalItem = { date: string; pnl: number };

const REGIME_KO: Record<string, string> = { BULL: "상승장", NEUTRAL: "중립장", BEAR: "하락장" };

/** '오늘 손익' 셀 — 금액(부호)과 전일 종가 대비 %. 값이 없으면(전일 종가 없음·시세 미연동) 한 줄 대시.
 *  compact=true 는 좁은 화면에서 누적 손익 아래에 붙는 보조 줄 (2026-09-10 지시). */
const dayTone = (v?: number | null) => ((v ?? 0) > 0 ? "text-up" : (v ?? 0) < 0 ? "text-down" : "text-faint");
const usd = (v: number) => `$${(v / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
// 오늘 손익의 라벨 — 기준일이 오면 '오늘' 대신 그 날짜를 쓴다 (2026-09-12).
// 주말·휴장·장 시작 전·일봉 적재 지연에는 마지막 거래일의 변동을 보여주므로, 오늘 것으로 오해하지 않게 날짜를 앞에 둔다.
const dayLabel = (asof?: string | null) => (asof ? `${Number(asof.slice(5, 7))}/${Number(asof.slice(8, 10))}` : "오늘");
function dayCell(v: number | null | undefined, pct: number | null | undefined, money: (n: number) => string,
                 compact = false, asof?: string | null) {
  const label = dayLabel(asof);
  if (v == null) return <span className="text-faint">{compact ? `${label} —` : "—"}</span>;
  const body = `${v >= 0 ? "+" : ""}${money(v)}${pct != null ? ` (${(pct * 100).toFixed(2)}%)` : ""}`;
  // 표 안(비compact)에서는 값만 보이므로, 오늘이 아닌 기준일이면 날짜를 앞에 붙여 열 머리글('오늘 손익')과 어긋나지 않게 한다
  return (
    <span className={compact ? dayTone(v) : undefined}>
      {compact ? `${label} ${body}` : (asof ? <><span className="text-faint">{label}</span> {body}</> : body)}
    </span>
  );
}
const REGIME_COLOR: Record<string, string> = { BULL: "var(--color-up)", NEUTRAL: "var(--color-accent)", BEAR: "var(--color-down)" };
const toneCls = { up: "text-up", down: "text-down", default: "text-muted" };

export default function DashboardPage() {
  const router = useRouter();
  const [loadError, setLoadError] = useState("");
  const [dash, setDash] = useState<Dash | null>(null);
  const [signal, setSignal] = useState<Signal | null>(null);
  const [range, setRange] = useState("3M");
  const [trendLegend, setTrendLegend] = useState<{ name: string; color: string }[]>([]);
  // 범례에서 고른 계열 — 그래프에 이름·마지막 값을 띄우고 나머지는 흐리게 (2026-09-10 지시)
  const [trendPick, setTrendPick] = useState<string | null>(null);
  const [trendTip, setTrendTip] = useState<TrendTip | null>(null);
  const [trendApprox, setTrendApprox] = useState(false);   // 매매일지 소급분 포함 여부 (2026-09-12)
  const trendRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);
  const trendSeries = useRef<TrendHandle[]>([]);

  const load = useCallback(async () => {
    const d = await apiFetch("/dashboard");
    if (d.ok) { setLoadError(""); setDash((await d.json()) as Dash); }
    else setLoadError(`대시보드 데이터를 불러오지 못했습니다 (HTTP ${d.status}) — 서버 로그·마이그레이션 상태를 확인하세요.`);
    const s = await apiFetch("/signals/daily");
    if (s.ok) setSignal((await s.json()) as Signal);
  }, []);

  // 실시간 총자산 (2026-09-10 지시) — 10초마다 가벼운 /dashboard/live 만 읽어 총액·전일 대비를 갱신한다.
  // 서버가 live_at 을 주지 않으면(장외·휴장) 멈추고, 화면이 숨겨져 있으면 건너뛴다
  const liveTick = useCallback(async () => {
    if (typeof document !== "undefined" && document.hidden) return;
    const r = await apiFetch("/dashboard/live");
    if (!r.ok) return;
    const j = (await r.json()) as Partial<Dash>;
    if (!j.live_at) return;
    setDash((prev) => (prev ? { ...prev, ...j } : prev));
  }, []);
  useEffect(() => {
    if (!dash?.live_at) return;
    const t = setInterval(() => { void liveTick(); }, 10_000);
    return () => clearInterval(t);
  }, [dash?.live_at, liveTick]);

  const disposeChart = useCallback(() => {
    try { chartApi.current?.remove(); } catch { /* already disposed */ }
    chartApi.current = null;
  }, []);

  // 포트별 다선 색 — 총자산(주황 면적) 외 KRW 포트 라인 (feature-dashboard §8, ADR-008)
  const SERIES_COLORS = ["#2563eb", "#059669", "#7c3aed", "#db2777", "#0891b2", "#ca8a04"];
  // 매매일지는 성격이 달라 파선 (2026-09-10). 일지별 선이 되면서(0028, 2026-09-12) 색도 나눈다 —
  // 실전매매 팔레트와 겹치지 않는 회청 계열
  // 앞 두 색이 실전매매 팔레트(파랑·초록)와 확실히 갈리도록 회청→장미 순. 청록은 초록과 붙어 보여 뒤로 뺐다
  const JOURNAL_COLORS = ["#475569", "#be123c", "#a21caf", "#a16207", "#0f766e"];

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
    const handles: TrendHandle[] = [];
    const total = chart.addSeries(AreaSeries, {
      lineColor: "#f97316", lineWidth: 2,
      topColor: "rgba(180,83,9,0.16)", bottomColor: "rgba(180,83,9,0.0)",
      priceLineVisible: false,
    });
    total.setData(items.map((i) => ({ time: i.date, value: i.total })));
    handles.push({ name: "총자산", color: "#f97316", kind: "total", api: total });
    // 실전매매 포트별 라인 + 매매일지 합계 — KRW 만 (US 는 센트 단위라 환율 도입 전 제외, ASSUMPTIONS 2026-09-02)
    const legend: { name: string; color: string }[] = [{ name: "총자산", color: "#f97316" }];
    let ci = 0, ji = 0;
    for (const sr of (body.series ?? []).filter((x) => x.currency === "KRW" && x.points.length >= 2)) {
      const isJournal = sr.kind === "journal";
      const color = isJournal ? JOURNAL_COLORS[ji++ % JOURNAL_COLORS.length] : SERIES_COLORS[ci++ % SERIES_COLORS.length];
      const line = chart.addSeries(LineSeries, {
        color, lineWidth: isJournal ? 2 : 1, priceLineVisible: false, lastValueVisible: false,
        lineStyle: isJournal ? LineStyle.Dashed : LineStyle.Solid,
      });
      line.setData(sr.points.map((pt) => ({ time: pt.date, value: pt.equity })));
      handles.push({ name: sr.name, color, kind: isJournal ? "journal" : "port", api: line });
      legend.push({ name: sr.name, color });
    }
    trendSeries.current = handles;
    setTrendApprox((body.series ?? []).some((x) => x.kind === "journal" && x.approx));
    setTrendPick(null);
    setTrendLegend(legend.length > 1 ? legend : []);
    // 커서가 가리키는 날짜의 계열별 값 (dataviz: 선·면 차트는 크로스헤어 읽기를 기본으로 둔다)
    chart.subscribeCrosshairMove((param) => {
      const box = trendRef.current;
      if (!box || !param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        setTrendTip(null);
        return;
      }
      const rows = handles
        .map((h) => {
          const d = param.seriesData.get(h.api) as { value?: number } | undefined;
          return d?.value == null ? null : { name: h.name, color: h.color, value: d.value };
        })
        .filter((r): r is { name: string; color: string; value: number } => r !== null)
        .sort((a, b) => b.value - a.value);
      if (!rows.length) { setTrendTip(null); return; }
      setTrendTip({ x: param.point.x, flip: param.point.x > box.clientWidth * 0.55,
        date: String(param.time), rows });
    });
    chart.timeScale().fitContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disposeChart]);

  // 범례 선택 → 그래프 표기 (2026-09-10 지시): 고른 계열만 이름·가격선·마지막 값을 켜고 나머지는 흐리게
  useEffect(() => {
    const fade = (hex: string) => {
      const n = parseInt(hex.slice(1), 16);
      return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, 0.2)`;
    };
    for (const h of trendSeries.current) {
      const on = !trendPick || h.name === trendPick;
      const sel = trendPick === h.name;
      const width = (sel ? 3 : h.kind === "port" ? 1 : 2) as LineWidth;
      const color = on ? h.color : fade(h.color);
      if (h.kind === "total") {
        (h.api as ISeriesApi<"Area">).applyOptions({
          lineColor: color, lineWidth: width, lastValueVisible: on, priceLineVisible: sel,
          title: sel ? h.name : "",
          topColor: on ? "rgba(180,83,9,0.16)" : "rgba(180,83,9,0.03)",
        });
      } else {
        (h.api as ISeriesApi<"Line">).applyOptions({
          color, lineWidth: width, lastValueVisible: sel, priceLineVisible: sel,
          title: sel ? h.name : "",
        });
      }
    }
  }, [trendPick]);

  useEffect(() => {
    void ensureSession().then((ok) => {
      if (!ok) { router.push("/login"); return; }
      void load().then(() => loadTrend(range));
    });
    return () => disposeChart();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 자산 구성 도넛 — 주식 · 현금 · 매매일지(취득원가) · 기타 (2026-09-05: 매매일지 분리)
  const jv = dash?.journal ?? 0;
  const donut = dash && dash.total > 0
    ? (() => {
        const a1 = (dash.stock / dash.total) * 360, a2 = a1 + (dash.cash / dash.total) * 360, a3 = a2 + (jv / dash.total) * 360;
        return `conic-gradient(var(--color-accent) 0 ${a1}deg, var(--color-down) ${a1}deg ${a2}deg, #0891b2 ${a2}deg ${a3}deg, #7c3aed ${a3}deg 360deg)`;
      })()
    : "var(--color-raised)";
  const ct = pnlTone(dash?.change_amount ?? 0);

  return (
    <main>
      <PageTitle title="대시보드" sub="총자산과 전략 상태를 한 화면에서 — 일별 스냅샷 기준, 지연 시세" />
      {loadError && <div className="mb-4 rounded-xl border border-down/40 bg-down/5 px-4 py-3 text-[14px] font-semibold text-down">⚠️ {loadError}</div>}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-6">
        {/* 1열 KPI 4카드 — 숫자+스파크라인, 자산 내용과의 중복 제거 (2026-09-05 지시).
            카드 위계 규칙 (2026-09-06): 총자산만 핵심(hero) 카드, 한국·미국 주식은 공용 Stat 19px — 네 페이지 동일 규칙 */}
        <Stat hero className="md:col-span-2" label="총자산 (KRW · 미국 자산 별도)" value={dash ? fmtWon(dash.total) : "—"}
          spark={dash?.total_trend ?? null}
          tip={<span>모든 실전매매·매매일지·기타 자산의 합입니다(미국 자산은 별도 카드).<br />
            <b>오늘</b> = 어제 총자산 대비 오늘의 변동, <b>누적</b> = 기록 시작 이후 증가액 — 둘 다 <b>입출금은 빼고</b> 순수 성과만 셉니다.<br />
            시장 카드의 누적(보유 원가 대비)과는 기준이 다릅니다. 구성은 오른쪽 자산 구성 카드에서 봅니다.</span>}
          sub={dash ? (<>
            {/* 오늘·누적 두 줄 (2026-09-10 지시) — 종전의 실전매매·매매일지 구성 줄은 자산 구성 카드와 계좌별 표에 있다 */}
            <span className="flex flex-wrap gap-x-3">
              <span className={`font-semibold ${toneCls[ct]}`}>
                {dash.change_amount >= 0 ? "▲" : "▼"} {dayLabel(dash.change_asof)} {fmtWon(Math.abs(dash.change_amount))} ({fmtPct(dash.change_pct, 2)})
                {dash.change_asof && <span className="ml-1 font-normal text-faint">종가 기준</span>}
              </span>
              {dash.live_at && <span className="text-ok" title="10초 간격 현재가로 평가 — 시세가 없는 종목은 종가 기준">● 실시간 {dash.live_at.slice(11, 16)}</span>}
            </span>
            <span className="mt-0.5 flex flex-wrap gap-x-3">
              {dash.since_inception_amount != null ? (
                <span className={`font-semibold ${toneCls[pnlTone(dash.since_inception_amount)]}`}>
                  {dash.since_inception_amount >= 0 ? "▲" : "▼"} 누적 {fmtWon(Math.abs(dash.since_inception_amount))} ({fmtPct(dash.since_inception_pct, 2)})
                </span>
              ) : <span className="text-faint">누적 — 기록 이틀째부터</span>}
            </span>
          </>) : undefined} />
        {/* 시장 카드도 총자산 카드와 같은 순서 — 오늘이 위, 누적이 아래 (2026-09-10 지시) */}
        <Stat className="md:col-span-1" label={<><MarketFlag market="KR" /> 한국 주식</>} value={dash ? fmtWon(dash.kr_stock.value) : "—"}
          spark={dash?.kr_trend ?? null} sparkColor="#2a78d6"
          tip={<span>국내 실전매매가 보유한 주식의 평가액입니다(현금 제외).<br /><b>누적</b>은 보유 원가 대비, <b>오늘</b>은 전일 종가 평가액 대비 — 분모가 다릅니다.</span>}
          sub={dash && dash.kr_stock.cost > 0 ? (<>
            <span className="block font-semibold">
              {dayCell(dash.kr_stock.day_change, dash.kr_stock.day_change_pct, fmtWon, true, dash.kr_stock.day_change_asof)}
            </span>
            <span className={`mt-0.5 block font-semibold ${toneCls[pnlTone(dash.kr_stock.pnl)]}`}>
              누적 {dash.kr_stock.pnl >= 0 ? "+" : ""}{fmtWon(dash.kr_stock.pnl)}
              {dash.kr_stock.pnl_pct != null && ` (${fmtPct(dash.kr_stock.pnl_pct, 2)})`}
            </span>
          </>) : undefined} />
        <Stat className="md:col-span-1" label={<><MarketFlag market="US" /> 미국 주식 ($)</>}
          value={dash ? `$${(dash.us_stock.value / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}` : "—"}
          spark={dash?.us_trend ?? null} sparkColor="#1baf7a"
          tip={<span>미국 실전매매가 보유한 주식의 평가액입니다(달러).<br /><b>누적</b>은 보유 원가 대비, <b>오늘</b>은 전일 종가 평가액 대비 — 분모가 다릅니다.</span>}
          sub={dash && dash.us_stock.cost > 0 ? (<>
            <span className="block font-semibold">
              {dayCell(dash.us_stock.day_change, dash.us_stock.day_change_pct, usd, true, dash.us_stock.day_change_asof)}
            </span>
            <span className={`mt-0.5 block font-semibold ${toneCls[pnlTone(dash.us_stock.pnl)]}`}>
              누적 {dash.us_stock.pnl >= 0 ? "+" : ""}${(dash.us_stock.pnl / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}
              {dash.us_stock.pnl_pct != null && ` (${fmtPct(dash.us_stock.pnl_pct, 2)})`}
            </span>
          </>) : undefined} />
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
          <div className="relative">
            <div ref={trendRef} className="h-52" />
            {/* 커서 날짜의 계열별 값 — 왼쪽 절반에서는 오른쪽에, 오른쪽 절반에서는 왼쪽에 붙인다 */}
            {trendTip && (
              <div className="pointer-events-none absolute top-1 z-10 min-w-[10rem] rounded-lg border border-line
                bg-surface/95 px-2.5 py-2 text-[12px] shadow-card"
                style={{ left: trendTip.x, transform: trendTip.flip ? "translateX(calc(-100% - 12px))" : "translateX(12px)" }}>
                <div className="mb-1 font-semibold text-ink">{trendTip.date}</div>
                {trendTip.rows.map((r, i) => (
                  <div key={i} className="flex items-center justify-between gap-3 leading-5">
                    {/* 이름이 길면 줄바꿈 대신 말줄임 — 금액 줄이 접히지 않게 (2026-09-10) */}
                    <span className="inline-flex min-w-0 items-center gap-1.5 text-muted">
                      <i className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: r.color }} />
                      <span className="max-w-[7.5rem] truncate">{r.name}</span>
                    </span>
                    <b className="whitespace-nowrap tabular-nums text-ink">{fmtWon(r.value)}</b>
                  </div>
                ))}
              </div>
            )}
          </div>
          {trendLegend.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1 text-[12.5px] text-muted">
              {/* 이름은 중복 가능(예: '내 계좌' 2개) — 위치 기반 키 (2026-09-05 중복 키 오류) */}
              {trendLegend.map((l, i) => {
                const on = trendPick === l.name;
                return (
                  <button key={i} type="button" aria-pressed={on} title={`${l.name} 강조`}
                    onClick={() => setTrendPick(on ? null : l.name)}
                    className={`inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 transition-colors
                      ${on ? "bg-raised font-semibold text-ink" : trendPick ? "text-faint hover:text-ink" : "hover:text-ink"}`}>
                    <i className="inline-block h-2 w-2 rounded-full" style={{ background: l.color }} />{l.name}
                  </button>
                );
              })}
              <span className="ml-1 text-[11.5px] text-faint">
                {trendPick ? "다시 누르면 전체 보기" : "항목을 누르면 그 선만 강조합니다"}
                {trendApprox && " · 매매일지 과거 구간은 기록·종가로 되살린 근사치"}
              </span>
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
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: "#0891b2" }} />매매일지 <b>{dash ? fmtWon(dash.journal ?? 0) : "—"}</b> <span className="text-[11.5px] text-faint">{(() => {
                // 평가 기준 표기 (2026-09-06): 총자산에 들어간 일지가 모두 현재가면 '평가액', 섞이면 '일부 취득원가', 아니면 '취득원가'
                const js = (dash?.journals ?? []).filter((x) => x.counted && (x.value ?? 0) > 0);
                if (js.length === 0) return "";
                const n = js.filter((x) => x.priced).length;
                return n === js.length ? "평가액" : n === 0 ? "취득원가" : "일부 취득원가";
              })()}</span></span>
              <span><i className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: "#7c3aed" }} />기타 <b>{dash ? fmtWon(dash.other) : "—"}</b></span>
            </div>
          </div>
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
                  <th className="pb-2 text-right font-medium">
                    <Tip tip={<span>매수 이후 누적 평가손익입니다.<br />%는 <b>보유 원가</b> 대비.</span>}>누적 손익 ⓘ</Tip>
                  </th>
                  <th className="hidden pb-2 text-right font-medium sm:table-cell">
                    <Tip tip={<span>오늘 하루의 평가 변동입니다 — 현재가(장중 10초 시세, 없으면 종가) − <b>전일 종가</b>.<br />%는 전일 종가 평가액 대비라 누적과 분모가 다릅니다.<br />전일 종가가 없는 종목(오늘 신규 매수 등)은 빠집니다.</span>}>오늘 손익 ⓘ</Tip>
                  </th>
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
                            {/* 좁은 화면에서는 오늘 손익을 아랫줄로 (열을 늘리면 잘린다, 2026-09-10) */}
                            <div className="text-[12px] font-normal sm:hidden">{dayCell(p.day_change, p.day_change_pct, money, true, p.day_change_asof)}</div>
                          </td>
                          <td className={`hidden table-num py-2.5 font-semibold sm:table-cell ${dayTone(p.day_change)}`}
                            title={(p.day_missing?.length ?? 0) > 0 ? `전일 종가가 없어 제외: ${p.day_missing!.join(", ")}` : undefined}>
                            {dayCell(p.day_change, p.day_change_pct, money, false, p.day_change_asof)}
                            {(p.day_missing?.length ?? 0) > 0 && <span className="text-[11.5px] font-normal text-faint"> *</span>}
                          </td>
                          <td className="py-1 pl-6">
                            {/* 이틀 이상 데이터가 있어야 선이 됨 — 첫날은 빈칸 대신 안내 (2026-09-05 지시) */}
                            <div className="w-28">
                              {(p.trend?.length ?? 0) >= 2
                                ? <Spark data={p.trend} className="h-7 w-full" />
                                : <span className="text-[11.5px] text-faint" title="내일 종가부터 추세가 그려집니다">첫날 · 추세 없음</span>}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* 매매일지 자산 — 진행 중 일지만, 취득원가 기준. 청산 일지는 표시하지 않음 (2026-09-05 지시) */}
        {(dash?.journals?.length ?? 0) > 0 && (
          <Card className="md:col-span-6">
            <CardTitle>매매일지 자산 <span className="normal-case text-faint">· 진행 중 일지 — 연결 계좌 현재가로 평가(없으면 취득원가), 이름 클릭 시 해당 일지로</span></CardTitle>
            <div className="overflow-x-auto">
              <table className="w-full whitespace-nowrap text-[14px]">
                <thead><tr className="border-b border-line text-left text-[12px] text-faint">
                  <th className="pb-2 pr-2 font-medium">#</th>
                  <th className="pb-2 font-medium">일지</th>
                  <th className="pb-2 text-right font-medium">평가액 <span className="font-normal">(원가)</span></th>
                  <th className="pb-2 text-right font-medium">
                    <Tip tip={<span>매수 이후 누적 평가손익입니다. %는 <b>보유 원가</b> 대비.</span>}>누적 손익 ⓘ</Tip>
                  </th>
                  <th className="hidden pb-2 text-right font-medium sm:table-cell">
                    <Tip tip={<span>오늘 하루의 평가 변동 — 현재가 − <b>전일 종가</b>. %는 전일 종가 평가액 대비.<br />총자산에 넣는 종목만 셉니다(실전매매와 겹쳐 제외한 종목은 빠짐).</span>}>오늘 손익 ⓘ</Tip>
                  </th>
                  <th className="pb-2 text-right font-medium">실현손익</th>
                  <th className="pb-2 pl-6 font-medium">총자산 포함</th>
                </tr></thead>
                <tbody>
                  {(dash!.journals ?? []).slice().sort((a, b) => (b.value ?? b.cost) - (a.value ?? a.cost)).map((j, i) => (
                    <tr key={j.id} className="border-b border-line/50 last:border-0">
                      <td className="py-2.5 pr-2 text-faint">{i + 1}</td>
                      <td className="py-2.5">
                        <Link href={`/mjournal?jid=${j.id}`} className="font-semibold underline-offset-2 hover:text-accent hover:underline">{j.name}</Link>
                        <div className="text-[12px] text-faint">
                          {j.holdings.length > 0 ? j.holdings.map((h) => `${h.symbol} ${h.qty.toLocaleString()}주`).join(" · ") : `보유 없음 · 기록 ${j.entries}건`}
                        </div>
                      </td>
                      <td className="table-num py-2.5 font-bold">{fmtWon(j.value ?? j.cost)}
                        {j.priced && <span className="ml-1 text-[11.5px] font-normal text-faint">({fmtWon(j.cost)})</span>}</td>
                      <td className={`table-num py-2.5 font-semibold ${(j.unrealized ?? 0) > 0 ? "text-up" : (j.unrealized ?? 0) < 0 ? "text-down" : "text-faint"}`}>
                        {j.priced && j.unrealized != null ? `${j.unrealized >= 0 ? "+" : ""}${fmtWon(j.unrealized)}${j.unrealized_pct != null ? ` (${(j.unrealized_pct * 100).toFixed(1)}%)` : ""}` : "—"}
                        {/* 좁은 화면에서는 오늘 손익을 아랫줄로 (2026-09-10) */}
                        <div className="text-[12px] font-normal sm:hidden">{dayCell(j.day_change, j.day_change_pct, fmtWon, true, j.day_change_asof)}</div></td>
                      <td className={`hidden table-num py-2.5 font-semibold sm:table-cell ${dayTone(j.day_change)}`}
                        title={(j.day_missing?.length ?? 0) > 0 ? `전일 종가가 없어 제외: ${j.day_missing!.join(", ")}` : undefined}>
                        {dayCell(j.day_change, j.day_change_pct, fmtWon, false, j.day_change_asof)}
                        {(j.day_missing?.length ?? 0) > 0 && <span className="text-[11.5px] font-normal text-faint"> *</span>}</td>
                      <td className={`table-num py-2.5 font-semibold ${j.realized > 0 ? "text-up" : j.realized < 0 ? "text-down" : "text-faint"}`}>
                        {j.realized !== 0 ? `${j.realized >= 0 ? "+" : ""}${fmtWon(j.realized)}` : "—"}
                        {j.return_pct != null && j.realized !== 0 && ` (${(j.return_pct * 100).toFixed(1)}%)`}
                      </td>
                      <td className="py-2.5 pl-6 text-[12.5px]">
                        {j.counted
                          ? ((j.excluded?.length ?? 0) > 0
                            ? <span className="text-warn" title={j.note ?? ""}>일부 포함 — {j.note}</span>
                            : <span className="text-ok">포함</span>)
                          : <span className="text-faint" title={j.note ?? ""}>제외 — {j.note}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

      </div>
    </main>
  );
}
