"use client";

/**
 * 차트 — Lightweight Charts v5: 캔들 + MA/EMA 오버레이 + 거래량 + RSI 페인 (feature-chart §5·§9).
 * 드로잉 v1 = 수평선 저장 (나머지 4종 TODO). 로그인 시 평단선 표시.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CandlestickSeries, createChart, HistogramSeries, IChartApi, IPriceLine, ISeriesApi, LineSeries,
} from "lightweight-charts";
import { ema, rsi, sma } from "../../lib/indicators";
import { apiFetch, ensureSession, hasToken } from "../../lib/api";


type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };

const PRESETS = [
  { code: "069500", label: "KODEX 200" },
  { code: "102110", label: "TIGER 200" },
  { code: "122630", label: "KODEX 레버리지" },
];
const MA_STYLES: [string, string, string][] = [
  ["ma20", "MA20", "#b45309"], ["ma60", "MA60", "#2563eb"], ["ma200", "MA200", "#7c3aed"], ["ema20", "EMA20", "#0e9f6e"],
];

export default function ChartPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const [code, setCode] = useState("069500");
  const [status, setStatus] = useState("");
  const [asOf, setAsOf] = useState<string | null>(null);
  const [hlines, setHlines] = useState<number[]>([]);
  const [newLine, setNewLine] = useState("");

  function disposeChart() {
    try { chartRef.current?.remove(); } catch { /* already disposed */ }
    chartRef.current = null;
    candleRef.current = null;
    priceLinesRef.current = [];
  }

  const load = useCallback(async (c: string) => {
    setStatus("불러오는 중…");
    const to = new Date().toISOString().slice(0, 10);
    const from = new Date(Date.now() - 3650 * 86400e3).toISOString().slice(0, 10);
    const res = await fetch(`/api/ohlcv?code=${c}&from=${from}&to=${to}&limit=10000`);
    if (!res.ok) { setStatus(`데이터 없음 (${res.status}) — 시세 시딩이 필요할 수 있습니다`); return; }
    const body = (await res.json()) as { items: Bar[]; as_of: string | null };
    setAsOf(body.as_of);
    if (body.items.length === 0) { setStatus("데이터 0건 — 시딩을 먼저 실행하세요"); return; }
    setStatus("");
    draw(body.items);
    if (hasToken()) {
      const d = await apiFetch(`/chart/drawings?code=${c}`);
      if (d.ok) {
        const items = ((await d.json()) as { items: { hlines?: number[] } }).items;
        setHlines(items.hlines ?? []);
        applyPriceLines(items.hlines ?? []);
      }
      const ps = await apiFetch("/portfolio/summary");
      if (ps.ok) {
        const positions = ((await ps.json()) as { positions: { code: string; avg_price: number; qty: number }[] }).positions;
        const mine = positions.find((x) => x.code === c);
        if (mine && candleRef.current) {
          candleRef.current.createPriceLine({ price: mine.avg_price, color: "#0e9f6e", lineWidth: 1, title: `평단 ${mine.qty}주` });
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyPriceLines(prices: number[]) {
    const candle = candleRef.current;
    if (!candle) return;
    priceLinesRef.current.forEach((l) => candle.removePriceLine(l));
    priceLinesRef.current = prices.map((p) =>
      candle.createPriceLine({ price: p, color: "#d92f45", lineWidth: 1, title: p.toLocaleString() }),
    );
  }

  function draw(bars: Bar[]) {
    if (!containerRef.current) return;
    disposeChart();
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "transparent" }, textColor: "#858c9b", attributionLogo: false, fontSize: 12 },
      grid: { vertLines: { color: "rgba(18,24,40,0.06)" }, horzLines: { color: "rgba(18,24,40,0.06)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      autoSize: true,
    });
    chartRef.current = chart;
    const closes = bars.map((b) => b.close);
    const times = bars.map((b) => b.date);

    // 국내 관례: 상승=적 / 하락=청 (REQUIREMENTS §7)
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: "#d92f45", wickUpColor: "#d92f45", borderUpColor: "#d92f45",
      downColor: "#2563eb", wickDownColor: "#2563eb", borderDownColor: "#2563eb",
    });
    candle.setData(bars.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })));
    candleRef.current = candle;

    const overlays: [(number | null)[], string][] = [
      [sma(closes, 20), MA_STYLES[0][2]], [sma(closes, 60), MA_STYLES[1][2]],
      [sma(closes, 200), MA_STYLES[2][2]], [ema(closes, 20), MA_STYLES[3][2]],
    ];
    for (const [values, color] of overlays) {
      chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, crosshairMarkerVisible: false })
        .setData(values.flatMap((v, i) => (v === null ? [] : [{ time: times[i], value: v }])));
    }

    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", color: "rgba(18,24,40,0.18)" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: b.date, value: b.volume })));

    chart.addSeries(LineSeries, { color: "#b45309", lineWidth: 1 }, 1)
      .setData(rsi(closes, 14).flatMap((v, i) => (v === null ? [] : [{ time: times[i], value: v }])));

    chart.timeScale().fitContent();
  }

  async function saveLines(next: number[]) {
    setHlines(next);
    applyPriceLines(next);
    if (hasToken()) {
      await apiFetch(`/chart/drawings?code=${code}`, { method: "PUT", body: JSON.stringify({ items: { hlines: next } }) });
    }
  }

  useEffect(() => {
    void ensureSession().then(() => load(code));  // 세션 복원 후 로드 — 평단선·드로잉 표시
    return () => disposeChart();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  return (
    <main className="flex h-[calc(100vh-130px)] flex-col">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded-lg border border-line">
          {PRESETS.map((p) => (
            <button key={p.code} onClick={() => setCode(p.code)}
              className={`px-3.5 py-2 text-[15px] font-semibold transition-colors ${
                code === p.code ? "bg-raised text-ink" : "bg-surface text-muted hover:text-ink"}`}>
              {p.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-faint">{asOf ? `기준시각 ${new Date(asOf).toLocaleString("ko-KR")} · 지연 시세` : ""}</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden gap-3 text-[11px] text-faint lg:flex">
            {MA_STYLES.map(([k, label, color]) => (
              <span key={k}><i className="mr-1 inline-block h-0.5 w-3 align-middle" style={{ background: color }} />{label}</span>
            ))}
          </span>
          <input className="input w-28 !py-2" placeholder="수평선 가격" value={newLine} onChange={(e) => setNewLine(e.target.value)} />
          <button className="btn !py-2" onClick={() => { const p = Number(newLine); if (p > 0) { void saveLines([...hlines, p]); setNewLine(""); } }}>추가</button>
          {hlines.length > 0 && <button className="btn-ghost btn !py-2" onClick={() => void saveLines([])}>지우기</button>}
        </div>
      </div>
      {status && <p className="mb-2 text-[13px] text-muted">{status}</p>}
      <div className="card flex-1 overflow-hidden p-2">
        <div ref={containerRef} className="h-full w-full" />
      </div>
    </main>
  );
}
