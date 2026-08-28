"use client";

/**
 * 차트 화면 v1 — Lightweight Charts v5 (feature-chart §5·§9).
 * 캔들 + MA20/60/200·EMA20 오버레이 + 거래량 + RSI 서브페인, 수평선 드로잉 저장(로그인 시).
 * 나머지 드로잉 4종(추세선·채널·피보나치·텍스트)은 백로그(TODO.md) — v1은 수평선만.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineSeries,
} from "lightweight-charts";
import { atr, ema, rsi, sma } from "../../lib/indicators";
import { apiFetch, hasToken } from "../../lib/api";

type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number };

const PRESETS = [
  { code: "069500", label: "KODEX 200" },
  { code: "102110", label: "TIGER 200" },
  { code: "122630", label: "KODEX 레버리지" },
];

const MA_COLORS: Record<string, string> = {
  ma20: "#e8b339", ma60: "#4fc3f7", ma200: "#ab7df8", ema20: "#66d9a8",
};

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

  const load = useCallback(async (c: string) => {
    setStatus("불러오는 중…");
    const to = new Date().toISOString().slice(0, 10);
    const from = new Date(Date.now() - 3650 * 86400e3).toISOString().slice(0, 10);
    const res = await fetch(`/api/ohlcv?code=${c}&from=${from}&to=${to}&limit=10000`);
    if (!res.ok) {
      setStatus(`데이터 없음 (${res.status}) — 시세 시딩이 필요할 수 있습니다`);
      return;
    }
    const body = (await res.json()) as { items: Bar[]; as_of: string | null };
    setAsOf(body.as_of);
    if (body.items.length === 0) {
      setStatus("데이터 0건 — .env에 KIS 키 기입 후 시딩을 실행하세요");
      return;
    }
    setStatus("");
    draw(body.items);
    if (hasToken()) {
      const d = await apiFetch(`/chart/drawings?code=${c}`);
      if (d.ok) {
        const items = ((await d.json()) as { items: { hlines?: number[] } }).items;
        setHlines(items.hlines ?? []);
        applyPriceLines(items.hlines ?? []);
      }
      // 실전 포지션 평단선 (feature-chart §5)
      const ps = await apiFetch("/portfolio/summary");
      if (ps.ok) {
        const positions = ((await ps.json()) as { positions: { code: string; avg_price: number; qty: number }[] }).positions;
        const mine = positions.find((x) => x.code === c);
        if (mine && candleRef.current) {
          candleRef.current.createPriceLine({ price: mine.avg_price, color: "#66d9a8", lineWidth: 1, title: `평단 ${mine.qty}주` });
        }
      }
    }
  }, []);

  function applyPriceLines(prices: number[]) {
    const candle = candleRef.current;
    if (!candle) return;
    priceLinesRef.current.forEach((l) => candle.removePriceLine(l));
    priceLinesRef.current = prices.map((p) =>
      candle.createPriceLine({ price: p, color: "#f2617a", lineWidth: 1, title: `${p.toLocaleString()}` }),
    );
  }

  function draw(bars: Bar[]) {
    if (!containerRef.current) return;
    chartRef.current?.remove();
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "#111117" }, textColor: "#c9c9d1", attributionLogo: false },
      grid: { vertLines: { color: "#22222c" }, horzLines: { color: "#22222c" } },
      autoSize: true,
    });
    chartRef.current = chart;
    const closes = bars.map((b) => b.close);
    const times = bars.map((b) => b.date);

    // 국내 관례: 상승=적 / 하락=청 (REQUIREMENTS §7)
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: "#e5484d", wickUpColor: "#e5484d", borderUpColor: "#e5484d",
      downColor: "#3b82f6", wickDownColor: "#3b82f6", borderDownColor: "#3b82f6",
    });
    candle.setData(bars.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })));
    candleRef.current = candle;

    const overlays: [string, (number | null)[]][] = [
      ["ma20", sma(closes, 20)], ["ma60", sma(closes, 60)], ["ma200", sma(closes, 200)], ["ema20", ema(closes, 20)],
    ];
    for (const [name, values] of overlays) {
      const s = chart.addSeries(LineSeries, { color: MA_COLORS[name], lineWidth: 1, priceLineVisible: false });
      s.setData(values.flatMap((v, i) => (v === null ? [] : [{ time: times[i], value: v }])));
    }

    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", color: "#3c3c49" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: b.date, value: b.volume })));

    const rsiSeries = chart.addSeries(LineSeries, { color: "#e8b339", lineWidth: 1 }, 1);
    rsiSeries.setData(rsi(closes, 14).flatMap((v, i) => (v === null ? [] : [{ time: times[i], value: v }])));

    chart.timeScale().fitContent();
    void atr; // 전략 오버레이(Phase 4)에서 사용 예정
  }

  async function saveLines(next: number[]) {
    setHlines(next);
    applyPriceLines(next);
    if (hasToken()) {
      await apiFetch(`/chart/drawings?code=${code}`, { method: "PUT", body: JSON.stringify({ items: { hlines: next } }) });
    }
  }

  useEffect(() => {
    void load(code);
    return () => chartRef.current?.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  return (
    <main style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8, height: "100vh" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {PRESETS.map((p) => (
          <button key={p.code} onClick={() => setCode(p.code)}
            style={{ padding: "6px 10px", background: code === p.code ? "#2d2d3a" : "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6 }}>
            {p.label}
          </button>
        ))}
        <span style={{ opacity: 0.6, fontSize: 12 }}>
          {asOf ? `기준시각 ${new Date(asOf).toLocaleString("ko-KR")} · 지연 시세` : ""}
        </span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <input value={newLine} onChange={(e) => setNewLine(e.target.value)} placeholder="수평선 가격"
            style={{ width: 110, background: "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "6px 8px" }} />
          <button onClick={() => { const p = Number(newLine); if (p > 0) { void saveLines([...hlines, p]); setNewLine(""); } }}
            style={{ padding: "6px 10px", background: "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6 }}>
            수평선 추가
          </button>
          <button onClick={() => void saveLines([])}
            style={{ padding: "6px 10px", background: "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6 }}>
            지우기
          </button>
        </span>
      </div>
      {status && <p style={{ opacity: 0.7 }}>{status}</p>}
      <div ref={containerRef} style={{ flex: 1, minHeight: 320 }} />
      <p style={{ opacity: 0.45, fontSize: 12, margin: 0 }}>
        모의·과거 데이터 기반이며 투자 권유가 아닙니다. 수평선 저장은 로그인 시 서버에 보관됩니다.
      </p>
    </main>
  );
}
