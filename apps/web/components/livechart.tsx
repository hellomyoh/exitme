"use client";
/** 실시간 현재가 vs 주문선 (2026-09-09 지시) — 10초 폴링 시계열(/quotes/series) + WS(/ws/quotes) 로 흐르는 라인,
 *  그리드 매수·익절·초기 진입·갭 기준을 점선 가격선으로, 옆에 줄별 현재가 대비 거리(%)를 틱마다 갱신한다.
 *  KIS 호출은 추가하지 않는다 — 기존 10초 폴링이 쌓은 값만 읽는다. */
import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, IChartApi, ISeriesApi, LineSeries, LineStyle, UTCTimestamp } from "lightweight-charts";
import { apiFetch } from "../lib/api";

export type OrderLine = { kind: string; label: string; side: "buy" | "sell" | "gap"; price: number };
type Pt = { t: number; p: number };

const KST_TIME = (t: number) => new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
const COLOR: Record<OrderLine["side"], string> = { buy: "#d92f45", sell: "#2563eb", gap: "#9aa1ad" };

export default function LiveChart({ code, name, lines, fpx }: { code: string; name: string; lines: OrderLine[]; fpx: (v: number) => string }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const [pts, setPts] = useState<Pt[]>([]);
  const [last, setLast] = useState<Pt | null>(null);
  const [follow, setFollow] = useState(true);      // 최근 60분 창을 오른쪽 끝에 붙여 흐르게
  const [status, setStatus] = useState<"loading" | "live" | "polling" | "empty">("loading");

  // 첫 로드: 오늘 시계열 (비어 있으면 서버가 1분봉으로 1회 백필)
  useEffect(() => {
    let alive = true;
    (async () => {
      const r = await apiFetch(`/quotes/series?code=${code}`);
      if (!alive) return;
      if (r.ok) {
        const j = (await r.json()) as { items: Pt[] };
        setPts(j.items);
        setLast(j.items.length ? j.items[j.items.length - 1] : null);
        setStatus(j.items.length ? "live" : "empty");
      } else setStatus("empty");
    })();
    return () => { alive = false; };
  }, [code]);

  // 실시간: WS 구독 → 틱마다 한 점. 끊기면 15초 폴링으로 대체
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    const push = (p: Pt) => {
      setPts((prev) => (prev.length && prev[prev.length - 1].t >= p.t ? prev : [...prev, p]));
      setLast(p);
    };
    const startPolling = () => {
      if (timer) return;
      setStatus("polling");
      timer = setInterval(async () => {
        const r = await apiFetch(`/quotes/series?code=${code}`);
        if (r.ok) { const j = (await r.json()) as { items: Pt[] }; if (j.items.length) { setPts(j.items); setLast(j.items[j.items.length - 1]); } }
      }, 15000);
    };
    try {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/quotes`);
      ws.onopen = () => { ws?.send(JSON.stringify({ subscribe: [code] })); setStatus("live"); };
      ws.onmessage = (ev) => {
        try {
          const q = JSON.parse(ev.data) as { code: string; price: number; as_of: string };
          if (q.code !== code) return;
          push({ t: Math.floor(new Date(q.as_of).getTime() / 1000), p: q.price });
        } catch { /* ignore */ }
      };
      ws.onerror = () => startPolling();
      ws.onclose = () => startPolling();
    } catch { startPolling(); }
    return () => { try { ws?.close(); } catch { /* noop */ } if (timer) clearInterval(timer); };
  }, [code]);

  // 차트 생성 (한 번) + 가격선
  useEffect(() => {
    if (!boxRef.current) return;
    const chart = createChart(boxRef.current, {
      layout: { background: { color: "transparent" }, textColor: "#9aa1ad", attributionLogo: false, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: "#eef0f3" } },
      height: 260, autoSize: true,
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 4,
        tickMarkFormatter: (t: number) => KST_TIME(t) },
      localization: { timeFormatter: (t: number) => KST_TIME(t), priceFormatter: (v: number) => Math.round(v).toLocaleString() },
    });
    const series = chart.addSeries(LineSeries, { color: "#17181c", lineWidth: 2, title: name, priceLineVisible: true, lastValueVisible: true });
    for (const ln of lines) {
      series.createPriceLine({ price: ln.price, color: COLOR[ln.side], lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: ln.label });
    }
    chartRef.current = chart; seriesRef.current = series;
    return () => { try { chart.remove(); } catch { /* noop */ } chartRef.current = null; seriesRef.current = null; };
  // 선(주문표)이 바뀌면 다시 그린다
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, JSON.stringify(lines)]);

  // 데이터 반영 + 창 이동
  useEffect(() => {
    const s = seriesRef.current, c = chartRef.current;
    if (!s || !c) return;
    s.setData(pts.map((p) => ({ time: p.t as UTCTimestamp, value: p.p })));
    if (!pts.length) return;
    if (follow) {
      const to = pts[pts.length - 1].t + 60;   // 오른쪽 여유 1분
      c.timeScale().setVisibleRange({ from: (to - 3600) as UTCTimestamp, to: to as UTCTimestamp });
    } else c.timeScale().fitContent();
  }, [pts, follow]);

  // 줄별 거리 — 현재가 대비 %, 가까운 순
  const dist = useMemo(() => {
    if (!last) return [];
    return lines.map((ln) => ({ ...ln, diff: ln.price - last.p, pct: (ln.price - last.p) / last.p * 100 }))
      .sort((a, b) => b.price - a.price);
  }, [lines, last]);
  const nearest = dist.length ? dist.reduce((a, b) => (Math.abs(b.pct) < Math.abs(a.pct) ? b : a)) : null;

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_260px]">
      <div>
        <div className="mb-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
          <span>{name} 현재가 <b className="text-ink">{last ? fpx(last.p) : "—"}</b>{last && <span className="text-faint"> ({KST_TIME(last.t)})</span>}</span>
          <span className={`rounded px-1.5 py-0.5 text-[11px] ${status === "live" ? "bg-ok/10 text-ok" : status === "polling" ? "bg-warn/10 text-warn" : "bg-raised text-faint"}`}>
            {status === "live" ? "● 실시간(10초)" : status === "polling" ? "○ 15초 폴링" : status === "loading" ? "불러오는 중" : "장중 데이터 없음"}
          </span>
          <button className="ml-auto text-[12px] text-accent hover:underline" onClick={() => setFollow((f) => !f)}>{follow ? "하루 전체 보기" : "최근 60분 따라가기"}</button>
        </div>
        <div ref={boxRef} className="h-[260px]" />
      </div>
      <div className="text-[13px]">
        <div className="mb-1 font-semibold text-ink">주문선까지 거리 <span className="font-normal text-faint">(현재가 대비)</span></div>
        {!last && <p className="text-faint">현재가가 들어오면 계산됩니다.</p>}
        <ul className="grid gap-1">
          {dist.map((d) => (
            <li key={`${d.kind}:${d.price}`} className={`flex items-center justify-between rounded-md border px-2.5 py-1.5 ${nearest === d ? "border-line-strong bg-inset font-semibold" : "border-line"}`}>
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block h-0 w-4 border-t-2 border-dashed" style={{ borderColor: COLOR[d.side] }} />
                {d.label} <span className="text-faint">{fpx(d.price)}</span>
              </span>
              <span className={d.pct >= 0 ? "text-up" : "text-down"}>{d.pct >= 0 ? "+" : ""}{d.pct.toFixed(2)}% <span className="text-faint">({d.diff >= 0 ? "+" : ""}{Math.round(d.diff).toLocaleString()})</span></span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[11.5px] leading-relaxed text-faint">빨강 = 매수 지정가(그리드·초기 진입), 파랑 = 익절 매도, 회색 = 갭 취소 기준. 09:01 무인 실행 중 1~2분은 폴링이 양보해 점이 비어 있을 수 있습니다.</p>
      </div>
    </div>
  );
}
