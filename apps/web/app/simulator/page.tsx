"use client";

/** 백테스트 3스텝 위저드 — 조건 → 실행(WS 진행률) → 결과 (feature-backtest §9). */
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AreaSeries, createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { DEFAULT_CAPITAL, fmtMoneyM, fmtPriceM, MARKET_LABEL, marketOf } from "../../lib/market";
import { Badge, Callout, Card, CardTitle, fmtPct, GaugeBar, PageTitle, Stat, Tip } from "../../components/ui";
import MarketSwitch from "../../components/marketswitch";

type Flags = Record<string, boolean>;
type Kpi = { total_return: number; cagr: number | null; mdd: number; sharpe: number | null; trades: number; win_rate: number | null; profit_factor: number | null };
type EquityPoint = { date: string; equity: number; benchmark: number; regime: string; exposure: number };
type Job = { id: number; status: string; progress: number; params: Record<string, unknown>; kpi: Kpi | null; equity?: EquityPoint[]; trades?: Record<string, unknown>[]; stale?: boolean };
type JournalOrder = { instrument: string; side: string; otype?: string; kind: string; price: number | null; qty: number };
type JournalDay = {
  date: string; regime: string; exposure: number; equity: number; day_return: number; day_pnl: number;
  total_return: number; cash: number; qty_200: number; qty_lev: number;
  planned: JournalOrder[]; fills: JournalOrder[];
};

const FLAG_LABELS: [string, string, string][] = [
  ["f1_no_tp_in_bull", "① 상승장 익절 제거", "체결분을 코어로 보유 — 기여도 최대"],
  ["f2_downside_vol", "② 하방 변동성 타게팅", "상승 변동성에 벌점 없음"],
  ["f3_fast_regime", "③ 레짐 판정 단축", "MA20>MA60 교차 (끄면 v1 기울기)"],
  ["f4_leverage", "④ 레버리지 모듈", "Emax 1.30 · E>1 초과분만"],
  ["f5_gap_filter", "⑤ 갭 필터 + 잔여예산", "갭 하락 방어 · 예산 초과 미발주"],
];
type EtfKey = "KODEX" | "TIGER" | "QQQ_QLD" | "QQQ_TQQQ" | "QQQ_TF";
const ETF_INFO: Record<EtfKey, { label: string; fee: number; market: "KR" | "US"; legacy?: boolean }> = {
  TIGER: { label: "TIGER 200 (기본)", fee: 0.0005, market: "KR" },
  KODEX: { label: "KODEX 200", fee: 0.0015, market: "KR" },
  QQQ_TF: { label: "QQQ 추세 필터 (미국 기본)", fee: 0.002, market: "US" },
  QQQ_QLD: { label: "QQQ + QLD — RAVG 전략 (비교용)", fee: 0.002, market: "US", legacy: true },
  QQQ_TQQQ: { label: "QQQ + TQQQ — RAVG 전략 (비교용)", fee: 0.002, market: "US", legacy: true },
};

export default function SimulatorPageWrapper() {
  return <Suspense fallback={null}><MarketKeyed /></Suspense>;
}

function MarketKeyed() {
  // 마켓 전환 시 페이지 상태 전체 리셋 — 이전 마켓의 결과·선택이 남는 것 방지 (2026-08-31 검토)
  const sp = useSearchParams();
  const resetKey = sp?.get("r") ?? "";
  return <SimulatorPage key={`${marketOf(sp)}-${resetKey}`} />;
}

function SimulatorPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const sp = useSearchParams();
  const market = marketOf(sp);
  const fm = (v: number) => fmtMoneyM(market, v);
  const fpx = (v: number) => fmtPriceM(market, v);
  const ETF_KEYS = (Object.keys(ETF_INFO) as EtfKey[]).filter((k) => ETF_INFO[k].market === market);  // RAVG 페어도 비교용으로 선택 가능 (2026-09-01 지시)
  const [etf, setEtf] = useState<EtfKey>(market === "US" ? "QQQ_TF" : "TIGER");  // KR 기본 TIGER (2026-09-01)
  // 자본 입력은 표기 통화 (미국: 달러) — 전송 시 API 단위(센트)로 변환
  const [capital, setCapital] = useState(market === "US" ? String(DEFAULT_CAPITAL.US / 100) : "100000000");
  const [dateFrom, setDateFrom] = useState(new Date(Date.now() - 365 * 86400e3).toISOString().slice(0, 10)); // 기본 1년 전 (2026-08-28 지시)
  const [dateTo, setDateTo] = useState(new Date().toISOString().slice(0, 10));
  const [flags, setFlags] = useState<Flags>(Object.fromEntries(FLAG_LABELS.map(([k]) => [k, true])));
  const [jobId, setJobId] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<Job[]>([]);
  const [overlay, setOverlay] = useState<number[]>([]);
  // 알고리즘 변수 수동 입력 (2026-09-02) — /settings/algorithm 레지스트리 기반
  type AlgoItem = { key: string; label: string; help: string; editable: boolean; default: number; value: number; min: number; max: number };
  const [algoItems, setAlgoItems] = useState<AlgoItem[]>([]);
  const [algoEdit, setAlgoEdit] = useState<Record<string, string>>({});
  const [algoOpen, setAlgoOpen] = useState(false);
  // 보유 상태로 시작 (2026-09-02)
  const [simHoldings, setSimHoldings] = useState<{ leg: "K200" | "LEV"; qty: string; price: string }[]>([]);
  const liveOverlayRef = useRef<{ name: string; points: { time: string; value: number }[] } | null>(null);
  const [journal, setJournal] = useState<JournalDay[] | null>(null);
  const [journalBusy, setJournalBusy] = useState(false);
  const [tradedOnly, setTradedOnly] = useState(true);
  const [visibleDays, setVisibleDays] = useState(30);
  const [show, setShow] = useState({ strategy: true, hold: false, trend: true });
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);
  const othersRef = useRef<Job[]>([]);

  useEffect(() => {
    void ensureSession().then((ok) => {
      if (!ok) { router.push("/login"); return; }
      void loadHistory();
    });
    return () => disposeChart();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function disposeChart() {
    try { chartApi.current?.remove(); } catch { /* already disposed */ }
    chartApi.current = null;
  }

  async function openAlgo() {
    setAlgoOpen(true);
    if (algoItems.length) return;
    const res = await apiFetch("/settings/algorithm");
    if (res.ok) {
      const items = (((await res.json()) as { items: AlgoItem[] }).items).filter((i) => i.editable);
      setAlgoItems(items);
      setAlgoEdit(Object.fromEntries(items.map((i) => [i.key, String(i.value)])));
    }
  }

  async function loadHistory() {
    const res = await apiFetch("/backtests");
    if (res.ok) setHistory(((await res.json()) as { items: Job[] }).items.filter((j) => j.status === "DONE"));
  }

  async function start() {
    setError("");
    const res = await apiFetch("/backtests", {
      method: "POST",
      body: JSON.stringify({
        capital: market === "US" ? Math.round(Number(capital) * 100) : Number(capital),
        date_from: dateFrom, date_to: dateTo, etf, flags,
        costs: { fee_200: ETF_INFO[etf].fee },
        // 변수 수동 오버라이드 — 기본값과 다른 항목만 (2026-09-02)
        algo: (() => {
          const out: Record<string, number> = {};
          for (const it of algoItems) {
            const v = Number(algoEdit[it.key]);
            if (Number.isFinite(v) && v !== it.default) out[it.key] = v;
          }
          return Object.keys(out).length ? out : undefined;
        })(),
        holdings: (() => {
          const rows = simHoldings
            .map((h) => ({ leg: h.leg, qty: Number(h.qty),
                           price: market === "US" ? Math.round(Number(h.price) * 100) : Number(h.price) }))
            .filter((h) => h.qty > 0 && h.price > 0);
          return rows.length ? rows : undefined;
        })(),
      }),
    });
    if (!res.ok) {
      setError(((await res.json()) as { detail?: string }).detail ?? `실행 실패 (${res.status})`);
      return;
    }
    const { id, reused } = (await res.json()) as { id: number; reused?: boolean };
    if (reused) {
      // 동일 조건·동일 데이터 — 기존 결과 재사용 (중복 기록 생성 안 함)
      await showResult(id);
      return;
    }
    setJobId(id); setProgress(0); setStep(2);
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/backtests/${id}`);
    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data as string) as { status: string; progress?: number; error?: string };
      if (msg.progress !== undefined) setProgress(msg.progress);
      if (msg.status === "DONE") { ws.close(); await showResult(id); }
      if (msg.status === "FAILED") { ws.close(); setError(msg.error ?? "실행 실패"); setStep(1); }
      if (msg.status === "CANCELED") { ws.close(); setStep(1); }
    };
  }

  async function showResult(id: number) {
    const res = await apiFetch(`/backtests/${id}`);
    if (!res.ok) return;
    const j = (await res.json()) as Job;
    setJob(j); setStep(3); setOverlay([]);
    // 이 백테스트에서 전환된 실전 포트가 있으면 실제 수익 곡선을 함께 그림 (2026-09-02 지시)
    liveOverlayRef.current = null;
    try {
      const pl = await apiFetch("/portfolios");
      if (pl.ok) {
        const mine = ((await pl.json()) as { items: { id: number; name: string; backtest_id: number | null }[] }).items;
        const linked = mine.find((x) => x.backtest_id === j.id);
        if (linked) {
          const eq = await apiFetch(`/portfolio/equity?portfolio_id=${linked.id}`);
          if (eq.ok) {
            const pts = ((await eq.json()) as { items: { date: string; index: number }[] }).items;
            if (pts.length >= 2) {
              liveOverlayRef.current = { name: linked.name, points: pts.map((x) => ({ time: x.date, value: x.index })) };
              setTimeout(() => drawEquity(j, othersRef.current), 80);  // 오버레이 로드 후 재드로우
            }
          }
        }
      }
    } catch { /* 비교 실패는 무시 */ }
    setJournal(null); setVisibleDays(30);
    void loadHistory();
    setTimeout(() => drawEquity(j, []), 60);
  }

  async function loadJournal(id: number) {
    setJournalBusy(true);
    const res = await apiFetch(`/backtests/${id}/journal`);
    if (res.ok) setJournal(((await res.json()) as { items: JournalDay[] }).items.reverse()); // 최근이 위
    setJournalBusy(false);
  }

  async function drawOverlay(ids: number[]) {
    if (!job) return;
    const others: Job[] = [];
    for (const id of ids) {
      const res = await apiFetch(`/backtests/${id}`);
      if (res.ok) others.push((await res.json()) as Job);
    }
    drawEquity(job, others);
  }

  function toggleSeries(key: "strategy" | "hold" | "trend", value: boolean) {
    const next = { ...show, [key]: value };
    setShow(next);
    if (job) drawEquity(job, othersRef.current, next);
  }

  function drawEquity(main: Job, others: Job[], opt = show) {
    if (!chartRef.current || !main.equity) return;
    othersRef.current = others;
    disposeChart();
    const chart = createChart(chartRef.current, {
      layout: { background: { color: "transparent" }, textColor: "#858c9b", attributionLogo: false, fontSize: 12 },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(18,24,40,0.07)" } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
      autoSize: true,
    });
    chartApi.current = chart;
    const norm = (pts: EquityPoint[], key: "equity" | "benchmark") => {
      const base = pts[0][key];
      return pts.map((p) => ({ time: p.date, value: (p[key] / base) * 100 }));
    };
    // 시리즈 on/off (2026-08-28 지시) — 전략 수익 / 매수보유 수익(같은 축 비교) / 종목 추세(하단)
    if (opt.strategy) {
      chart.addSeries(LineSeries, { color: "#f97316", lineWidth: 2, title: "전략" }).setData(norm(main.equity, "equity"));
      if (liveOverlayRef.current) {
        chart.addSeries(LineSeries, { color: "#0e9f6e", lineWidth: 2, title: `실전 ${liveOverlayRef.current.name}` })
          .setData(liveOverlayRef.current.points);
      }
    }
    if (opt.hold) {
      chart.addSeries(LineSeries, { color: "#64748b", lineWidth: 1, title: "매수보유" }).setData(norm(main.equity, "benchmark"));
    }
    if (opt.trend) {
      // 종목 추세는 하단 서브페인 — 수익 곡선을 가리지 않음
      chart.addSeries(AreaSeries, {
        lineColor: "#64748b", lineWidth: 1, title: "종목 추세",
        topColor: "rgba(100,116,139,0.14)", bottomColor: "rgba(100,116,139,0.0)",
        priceLineVisible: false,
      }, 1).setData(norm(main.equity, "benchmark"));
    }
    const colors = ["#2563eb", "#7c3aed", "#0e9f6e", "#d92f45"];
    others.forEach((o, idx) => {
      if (o.equity) chart.addSeries(LineSeries, { color: colors[idx % 4], lineWidth: 1, title: `#${o.id}` }).setData(norm(o.equity, "equity"));
    });
    chart.timeScale().fitContent();
  }

  function downloadCsv() {
    if (!job?.trades) return;
    const head = "instrument,kind,qty,buy_price,sell_price,buy_index,sell_index,pnl";
    const rows = job.trades.map((t) => head.split(",").map((k) => String(t[k])).join(","));
    const blob = new Blob([`${head}\n${rows.join("\n")}`], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `backtest-${job.id}-trades.csv`;
    a.click();
  }

  function clone(j: Job) {
    const p = j.params as { capital: number; date_from: string; date_to: string; etf?: string; flags?: Flags };
    setCapital(market === "US" ? String(p.capital / 100) : String(p.capital)); setDateFrom(p.date_from); setDateTo(p.date_to);
    if (p.etf && p.etf in ETF_INFO) setEtf(p.etf as EtfKey);
    if (p.flags) setFlags(p.flags);
    setStep(1);
  }

  const kpi = job?.kpi;

  return (
    <main>
      <PageTitle title={`시뮬레이터 · ${MARKET_LABEL[market]}`} sub="RAVG v2.5 백테스트 — 조건 설정 → 실행 → 결과. 모의 계산이며 투자 권유가 아닙니다." />
      <MarketSwitch />

      {/* 스텝 인디케이터 */}
      <div className="mb-5 flex items-center gap-2 text-[13px]">
        {["조건 설정", "실행", "결과"].map((label, i) => (
          <div key={label} className="flex items-center gap-2">
            {i > 0 && <span className="h-px w-8 bg-line-strong" />}
            <span className={`flex items-center gap-2 rounded-full px-3 py-1 font-semibold ${
              step === i + 1 ? "bg-accent text-white" : step > i + 1 ? "bg-raised text-ink" : "bg-raised/50 text-faint"}`}>
              <span>{i + 1}</span>{label}
            </span>
          </div>
        ))}
      </div>

      {step === 1 && (
        <div className="grid min-h-[60vh] items-start gap-4 lg:grid-cols-2">
          <div className="grid gap-4">
          <Card>
            <CardTitle>주력 ETF</CardTitle>
            <div className="grid grid-cols-2 gap-2">
              {ETF_KEYS.map((k) => (
                <button key={k} onClick={() => setEtf(k)}
                  className={`rounded-xl border p-4 text-left transition-colors ${etf === k ? "border-accent bg-accent-dim" : "border-line bg-inset hover:border-line-strong"}`}>
                  <div className="font-bold">{ETF_INFO[k].label}</div>
                  <div className="mt-0.5 text-xs text-faint">총보수 연 {(ETF_INFO[k].fee * 100).toFixed(2)}%{ETF_INFO[k].market === "KR" ? " · 레버리지는 KODEX 공통" : ETF_INFO[k].legacy ? " · 그리드+레버리지 (미국 비권장 — 비교 검증용)" : " · MA200 위 전량 보유 / 2% 관통 시 전량 현금"}</div>
                </button>
              ))}
            </div>
          </Card>
          <Card>
            <CardTitle>기간 · 자본</CardTitle>
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="grid gap-1 text-xs text-faint">시작일
                <input type="date" className="input" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
              <label className="grid gap-1 text-xs text-faint">종료일
                <input type="date" className="input" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
              <label className="grid gap-1 text-xs text-faint">자본금({market === "US" ? "$" : "원"})
                <input className="input" value={capital} onChange={(e) => setCapital(e.target.value)} /></label>
            </div>
          </Card>

          <Card className="mb-4">
            <details>
              <summary className="cursor-pointer text-[14px] font-semibold text-accent">보유 상태로 시작 (선택) — 실전과 동일하게 현재 보유를 넣고 시뮬레이션</summary>
              <p className="mb-2 mt-2 text-[13px] text-faint">자본금은 <b className="text-muted">현금</b>으로 별도 계상됩니다. 보유는 시작 시점 로트로 등록되어 첫날부터 전략 규칙(익절·축소)의 대상이 됩니다.</p>
              {simHoldings.map((h, i) => (
                <div key={i} className="mb-2 flex flex-wrap items-center gap-2">
                  <select className="input !py-2" value={h.leg}
                    onChange={(e) => setSimHoldings(simHoldings.map((x, j) => j === i ? { ...x, leg: e.target.value as "K200" | "LEV" } : x))}>
                    <option value="K200">{market === "US" ? "QQQ" : "주력 200 ETF"}</option>
                    {etf !== "QQQ_TF" && <option value="LEV">레버리지</option>}
                  </select>
                  <input className="input w-28 !py-2" placeholder="수량(주)" value={h.qty}
                    onChange={(e) => setSimHoldings(simHoldings.map((x, j) => j === i ? { ...x, qty: e.target.value } : x))} />
                  <input className="input w-40 !py-2" placeholder={market === "US" ? "평단($)" : "평단(원)"} value={h.price}
                    onChange={(e) => setSimHoldings(simHoldings.map((x, j) => j === i ? { ...x, price: e.target.value } : x))} />
                  <button className="btn-ghost btn !px-2 !py-1.5" onClick={() => setSimHoldings(simHoldings.filter((_, j) => j !== i))}>✕</button>
                </div>
              ))}
              <button className="btn-ghost btn w-fit !py-1.5 text-[13.5px]"
                onClick={() => setSimHoldings([...simHoldings, { leg: "K200", qty: "", price: "" }])}>＋ 보유 추가</button>
            </details>
          </Card>

          <Card className="mb-4">
            <details open={algoOpen} onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) void openAlgo(); }}>
              <summary className="cursor-pointer text-[14px] font-semibold text-accent">고급 — 알고리즘 변수 (선택) · 기본값과 다르게 입력한 항목만 이번 실행에 적용</summary>
              {algoItems.length === 0 ? (
                <p className="mt-2 text-[13px] text-faint">변수 목록을 불러오는 중…</p>
              ) : (
                <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-3">
                  {algoItems.map((it) => (
                    <label key={it.key} className="grid gap-1 text-[12.5px] text-faint" title={it.help}>
                      <span>{it.label} <span className="text-[11px]">({it.min}~{it.max} · 기본 {it.default})</span></span>
                      <input className={`input !py-1.5 ${Number(algoEdit[it.key]) !== it.default ? "border-accent" : ""}`}
                        value={algoEdit[it.key] ?? ""}
                        onChange={(e) => setAlgoEdit({ ...algoEdit, [it.key]: e.target.value })} />
                    </label>
                  ))}
                </div>
              )}
            </details>
          </Card>
          <Card>
            <CardTitle>절제(Ablation) 플래그 <span className="normal-case text-faint">· 하나씩 꺼서 모듈 기여 검증</span></CardTitle>
            <div className="grid gap-1.5">
              {FLAG_LABELS.map(([key, label, desc]) => (
                <label key={key} className={`flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 transition-colors hover:bg-raised ${
                  key !== "f4_leverage" || flags.f4_leverage ? "" : "opacity-60"}`}>
                  <span className="flex items-center gap-3">
                    <input type="checkbox" className="accent-[#f97316]" checked={flags[key]}
                      onChange={(e) => setFlags({ ...flags, [key]: e.target.checked })} />
                    <span className="font-medium">{label}</span>
                  </span>
                  <span className="text-xs text-faint">{desc}</span>
                </label>
              ))}
            </div>
            {!flags.f4_leverage && <p className="mt-2 text-xs text-warn">④ off — 레버리지 2트랙·강제청산 규칙이 비활성됩니다 (E ≤ 1.0)</p>}
          </Card>
          {error && <Callout icon="⛔">{error}</Callout>}
          <button className="btn btn-primary py-3 text-[15px]" onClick={() => void start()}>백테스트 실행 →</button>
          </div>
          <div className="grid gap-4">
          {history.length > 0 && (
            <Card>
              <CardTitle>지난 결과</CardTitle>
              <div className="grid gap-1">
                {history.slice(0, 8).map((j) => {
                  const p = j.params as { etf?: string; capital?: number; date_from?: string; date_to?: string; flags?: Flags };
                  const jobMarket = (p.etf ?? "KODEX").startsWith("QQQ") ? "US" : "KR";
                  if (jobMarket !== market) return null;
                  const offFlags = p.flags ? FLAG_LABELS.filter(([k]) => p.flags![k] === false).map(([, l]) => l.slice(0, 1)) : [];
                  return (
                    <div key={j.id} className="rounded-lg px-2 py-2 hover:bg-raised">
                      <div className="flex items-center gap-3 text-[14px]">
                        <span className="w-10 text-faint">#{j.id}</span>
                        <Badge tone="default">{p.etf ?? "KODEX"}</Badge>
                        <span className={`w-20 text-right font-bold ${(j.kpi?.total_return ?? 0) >= 0 ? "text-up" : "text-down"}`}>
                          {fmtPct(j.kpi?.total_return)}
                        </span>
                        <span className="ml-auto flex gap-1.5">
                          <button className="btn !px-2.5 !py-1 text-[12.5px]" onClick={() => void showResult(j.id)}>보기</button>
                          <button className="btn-ghost btn !px-2.5 !py-1 text-[12.5px]" onClick={() => clone(j)}>복제</button>
                          <button className="btn-ghost btn !px-2.5 !py-1 text-[12.5px] !text-up"
                            onClick={() => void (async () => {
                              if (!window.confirm(`백테스트 #${j.id} 기록을 삭제할까요? 되돌릴 수 없습니다.`)) return;
                              const r = await apiFetch(`/backtests/${j.id}`, { method: "DELETE" });
                              if (r.ok) { if (job?.id === j.id) { setJob(null); setStep(1); } void loadHistory(); }
                            })()}>삭제</button>
                        </span>
                      </div>
                      <div className="mt-1 pl-10 text-[12.5px] text-faint">
                        {p.date_from} ~ {p.date_to} · 자본 {p.capital ? fm(p.capital) : "—"}
                        {offFlags.length > 0 && <span className="text-warn"> · 절제 OFF: {offFlags.join(" ")}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>
          )}
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="grid min-h-[60vh] items-start">
          <Card>
            <CardTitle>백테스트 실행 중</CardTitle>
            <div className="mb-2 text-3xl font-extrabold">{progress}%</div>
            <GaugeBar ratio={progress / 100} height={10} />
            <button className="btn mt-5" onClick={() => jobId && void apiFetch(`/backtests/${jobId}/cancel`, { method: "POST" })}>취소</button>
          </Card>
        </div>
      )}

      {step === 3 && job && (
        <div className="grid gap-4">
          {(() => {
            const p = job.params as { etf?: string; capital?: number; date_from?: string; date_to?: string; flags?: Flags };
            const offFlags = p.flags ? FLAG_LABELS.filter(([k]) => p.flags![k] === false).map(([, l]) => l) : [];
            // 현재(최종) 평가액 — 자산곡선 마지막 값. 보유 시작 포함 실제 평가 기준 (2026-09-02 지시)
            const finalEq = job.equity?.length ? job.equity[job.equity.length - 1].equity : null;
            const tr = job.kpi?.total_return;
            const profit = finalEq != null && tr != null ? finalEq - finalEq / (1 + tr) : null;
            return (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[14px] text-muted">
                <Badge tone="default">#{job.id}</Badge>
                <span>주력 ETF <b className="text-ink">{ETF_INFO[(p.etf ?? "KODEX") as EtfKey]?.label ?? p.etf}</b></span>
                <span>기간 <b className="text-ink">{p.date_from} ~ {p.date_to}</b></span>
                <span>자본금 <b className="text-ink">{p.capital != null ? fm(p.capital) : "—"}</b></span>
                {finalEq != null && (
                  <span>현재 평가액 <b className="text-ink">{fm(Math.round(finalEq))}</b>
                    {profit != null && tr != null && (
                      <b className={profit >= 0 ? "text-up" : "text-down"}>
                        {" "}({profit >= 0 ? "+" : ""}{fm(Math.round(profit))} · {profit >= 0 ? "+" : ""}{(tr * 100).toFixed(1)}%)
                      </b>
                    )}
                  </span>
                )}
                <span>{offFlags.length > 0
                  ? <span className="text-warn">절제 OFF: {offFlags.join(", ")}</span>
                  : "전 모듈 ON (RAVG v2.5 기본)"}</span>
              </div>
            );
          })()}
          {job.stale && <Callout icon="⚠️">시세 데이터가 갱신되었습니다(stale) — 재실행을 권장합니다.</Callout>}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
            <Stat label="총수익률" value={fmtPct(kpi?.total_return)} tone={(kpi?.total_return ?? 0) >= 0 ? "up" : "down"}
              tip="기간 전체 누적 수익률 — (최종 평가액 ÷ 투입 자본) − 1. 비용(수수료·슬리피지·세금) 반영"
              spark={job.equity?.map((e) => e.equity)} />
            <Stat label="CAGR" value={kpi?.cagr === null ? "1년 미만" : fmtPct(kpi?.cagr)}
              tip="연평균 복리 수익률 — 매년 같은 비율로 늘었다고 환산한 값. 전략이 실제로 매매한 활동 구간 기준이며, 1년(252거래일) 미만이면 왜곡이 커서 표시하지 않습니다" />
            <Stat label="MDD" value={fmtPct(kpi?.mdd)} tone="down"
              tip="최대 낙폭 — 기간 중 고점 대비 가장 깊게 빠졌던 비율. 이 전략이 견뎌야 했던 최악의 순간으로, 작을수록(0에 가까울수록) 안전" />
            <Stat label="샤프" value={kpi?.sharpe?.toFixed(2) ?? "—"}
              tip="위험 대비 수익 효율 — 수익률을 변동성으로 나눈 값. 1 이상이면 준수, 2 이상이면 우수. 수익이 같아도 출렁임이 크면 낮아집니다" />
            <Stat label="거래" value={String(kpi?.trades ?? 0)} hint="FIFO 라운드트립"
              tip="완결된 매매(라운드트립) 횟수 — 매수 후 그 물량을 전부 매도해야 1건. 먼저 산 것부터 파는(FIFO) 기준으로 짝을 맞춥니다. 아직 팔지 않은 보유분은 제외" />
            <Stat label="승률" value={fmtPct(kpi?.win_rate)}
              tip="이익으로 끝난 라운드트립의 비율. 그리드 전략 특성상 승률이 높아도 총수익은 손익비와 함께 봐야 합니다" />
            <Stat label="손익비" value={kpi?.profit_factor?.toFixed(2) ?? "—"}
              tip="총이익 ÷ 총손실 — 번 돈이 잃은 돈의 몇 배인지. 1보다 커야 남는 장사이고, 클수록 좋습니다" />
          </div>
          <Card>
            <CardTitle right={
              <span className="flex items-center gap-4 text-[13px] font-normal normal-case text-muted">
                <label className="flex cursor-pointer items-center gap-1.5">
                  <input type="checkbox" className="accent-[#f97316]" checked={show.strategy}
                    onChange={(e) => toggleSeries("strategy", e.target.checked)} />
                  <i className="inline-block h-0.5 w-4 bg-accent" />전략 수익
                </label>
                <label className="flex cursor-pointer items-center gap-1.5">
                  <input type="checkbox" className="accent-[#64748b]" checked={show.hold}
                    onChange={(e) => toggleSeries("hold", e.target.checked)} />
                  <i className="inline-block h-0.5 w-4 bg-[#64748b]" />매수보유 수익
                </label>
                <label className="flex cursor-pointer items-center gap-1.5">
                  <input type="checkbox" className="accent-[#64748b]" checked={show.trend}
                    onChange={(e) => toggleSeries("trend", e.target.checked)} />
                  <span className="inline-block h-2 w-4 rounded-sm bg-[rgba(100,116,139,0.3)]" />종목 추세 (하단)
                </label>
              </span>
            }>자산곡선 <span className="normal-case text-faint">· 초기자본 = 100 정규화</span></CardTitle>
            <div ref={chartRef} className="h-96" />
            <p className="mt-2 text-[12.5px] leading-relaxed text-faint">
              <b className="text-muted">전략 수익</b> = RAVG v2.5를 따랐을 때의 자산 곡선 ·
              <b className="text-muted"> 매수보유 수익</b> = 같은 돈으로 종목을 사서 계속 들고 있었을 때(벤치마크, 같은 축 비교용) ·
              <b className="text-muted"> 종목 추세</b> = 종목 가격 흐름(하단 별도 영역이라 수익 곡선을 가리지 않음).
              체크박스로 켜고 끌 수 있습니다.
            </p>
          </Card>
          <Card>
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn" onClick={downloadCsv}>거래내역 CSV</button>
              <button className="btn" onClick={() => clone(job)}>이 조건으로 다시 설정</button>
              <button className="btn btn-primary" onClick={() => void (async () => {
                const r = await apiFetch(`/portfolios/from-backtest/${job.id}`, { method: "POST" });
                if (r.ok) {
                  // 전환된 새 포트가 바로 선택되게 pid 전달 — '내 계좌'로 떨어지던 결함 (2026-09-05 지시)
                  const { id } = (await r.json()) as { id: number };
                  router.push(`/portfolio?${market === "US" ? "market=US&" : ""}pid=${id}`);
                }
              })()}>실전매매로 전환 →</button>
              <span className="ml-auto flex flex-wrap items-center gap-2 text-[13px] text-muted">
                <span className="text-faint">오버레이 = 지난 백테스트를 같은 차트에 겹쳐 비교 (최대 4개):</span>
                {history.filter((h) => h.id !== job.id).slice(0, 8).map((h) => (
                  <label key={h.id} className="flex cursor-pointer items-center gap-1">
                    <input type="checkbox" className="accent-[#f97316]" checked={overlay.includes(h.id)}
                      disabled={!overlay.includes(h.id) && overlay.length >= 4}
                      onChange={(e) => {
                        const next = e.target.checked ? [...overlay, h.id] : overlay.filter((x) => x !== h.id);
                        setOverlay(next); void drawOverlay(next);
                      }} />#{h.id}
                  </label>
                ))}
              </span>
            </div>
          </Card>

          {/* 일자별 매매 기록 (2026-08-28 지시) — 장 시작 전 주문표 + 체결 + 수익률·보유 */}
          <Card>
            <CardTitle right={journal ? (
              <label className="flex items-center gap-1.5 text-[13px] font-normal normal-case text-muted">
                <input type="checkbox" className="accent-[#f97316]" checked={tradedOnly}
                  onChange={(e) => { setTradedOnly(e.target.checked); setVisibleDays(30); }} />
                거래 있는 날만
              </label>
            ) : undefined}>일자별 매매 기록</CardTitle>
            {!journal ? (
              <button className="btn" disabled={journalBusy} onClick={() => void loadJournal(job.id)}>
                {journalBusy ? "불러오는 중…" : "매매 기록 불러오기"}
              </button>
            ) : (() => {
              const rows = journal.filter((d) => !tradedOnly || d.planned.length > 0 || d.fills.length > 0);
              const shown = rows.slice(0, visibleDays);
              return (
                <div className="grid gap-1.5">
                  {shown.map((d) => (
                    <details key={d.date} className="rounded-xl border border-line bg-inset">
                      <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-4 py-3 text-[14.5px] transition-colors hover:bg-raised/60">
                        <b className="w-24">{d.date}</b>
                        <Badge tone={d.regime === "BULL" ? "up" : d.regime === "BEAR" ? "down" : "accent"}>
                          {d.regime === "BULL" ? "상승" : d.regime === "BEAR" ? "하락" : "중립"}
                        </Badge>
                        <span className={`w-20 text-right font-bold ${d.day_return > 0 ? "text-up" : d.day_return < 0 ? "text-down" : "text-muted"}`}>
                          {(d.day_return * 100).toFixed(2)}%
                        </span>
                        <span className={`hidden w-28 text-right text-[13px] font-semibold sm:inline ${d.day_pnl > 0 ? "text-up" : d.day_pnl < 0 ? "text-down" : "text-faint"}`}>
                          {d.day_pnl >= 0 ? "+" : ""}{fm(d.day_pnl)}
                        </span>
                        <span className="hidden text-[13px] text-faint sm:inline">누적 {(d.total_return * 100).toFixed(1)}%</span>
                        <span className="hidden text-[13px] text-muted md:inline">평가 {fm(d.equity)}</span>
                        <span className="hidden text-[13px] text-faint lg:inline">보유 200ETF {d.qty_200.toLocaleString()} · 레버 {d.qty_lev.toLocaleString()}</span>
                        <span className="ml-auto text-[13px] text-faint">주문 {d.planned.length} · 체결 {d.fills.length}</span>
                      </summary>
                      <div className="grid gap-x-8 gap-y-4 border-t-2 border-line-strong px-4 py-4 lg:grid-cols-2">
                        <JournalOrders title="📋 장 시작 전 주문표 (계획)" orders={d.planned} fill={false} fpx={fpx} fm={fm} />
                        <div className="border-t border-dashed border-line-strong pt-4 lg:border-l lg:border-t-0 lg:border-dashed-0 lg:pl-8 lg:pt-0" style={{ borderLeftStyle: "solid" }}>
                          <JournalOrders title="✅ 체결 내역" orders={d.fills} fill={true} fpx={fpx} fm={fm}
                            extra={d.fills.length > 0 ? (
                              <Tip tip={<span>당일 손익 = 그날 종가 평가액 − 전일 종가 평가액.<br />보유분의 평가손익 변화 + 그날 체결의 실현손익 − 수수료가 모두 합산된 값입니다 — 체결 건들만의 합계가 아닙니다.</span>}>
                                <span className={`font-bold ${d.day_pnl > 0 ? "text-up" : d.day_pnl < 0 ? "text-down" : "text-muted"}`}>
                                  당일 손익 {d.day_pnl >= 0 ? "+" : ""}{fm(d.day_pnl)} <span className="font-normal text-faint">ⓘ</span>
                                </span>
                              </Tip>
                            ) : undefined} />
                        </div>
                        <div className="col-span-full flex flex-wrap gap-x-6 gap-y-1 border-t border-line pt-3 text-[13.5px] text-muted">
                          <span>노출 E <b className="text-ink">{(d.exposure * 100).toFixed(1)}%</b></span>
                          <span>현금 <b className="text-ink">{fm(d.cash)}</b></span>
                          <span>보유 200 ETF <b className="text-ink">{d.qty_200.toLocaleString()}주</b></span>
                          <span>레버리지 <b className="text-ink">{d.qty_lev.toLocaleString()}주</b></span>
                        </div>
                      </div>
                    </details>
                  ))}
                  {rows.length > visibleDays && (
                    <button className="btn mt-1" onClick={() => setVisibleDays(visibleDays + 60)}>
                      더 보기 ({rows.length - visibleDays}일 남음)
                    </button>
                  )}
                  {rows.length === 0 && <p className="text-[13.5px] text-faint">표시할 기록이 없습니다.</p>}
                </div>
              );
            })()}
          </Card>
        </div>
      )}
    </main>
  );
}

const KIND_KO_J: Record<string, string> = {
  grid1: "그리드 1차", grid2: "그리드 2차", grid3: "그리드 3차", tp: "익절", reduce: "축소",
  lev_strat: "레버 전략", lev_tact1: "레버 전술1", lev_tact2: "레버 전술2",
  lev_tact_exit: "전술 이탈", lev_liq: "레버 청산",
};

function JournalOrders({ title, orders, fill, extra, fpx, fm }: { title: string; orders: JournalOrder[]; fill: boolean; extra?: React.ReactNode; fpx: (v: number) => string; fm: (v: number) => string }) {
  if (!fill) {
    // 계획 표시는 동일 (구분·종목·방향·가격) 병합 — 로트별 익절이 같은 가격이면 한 주문으로 (2026-08-31 검토)
    const merged = new Map<string, JournalOrder>();
    for (const o of orders) {
      const key = `${o.kind}|${o.instrument}|${o.side}|${o.price ?? "mkt"}`;
      const prev = merged.get(key);
      if (prev) merged.set(key, { ...prev, qty: prev.qty + o.qty });
      else merged.set(key, { ...o });
    }
    orders = Array.from(merged.values());
  }
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-[13.5px] font-semibold text-muted">
        <span>{title} ({orders.length}건)</span>{extra}
      </div>
      {orders.length === 0 ? <p className="text-[13px] text-faint">없음</p> : (
        <table className="w-full text-[14px]">
          <thead><tr className="text-left text-xs text-faint">
            <th className="pb-1 font-medium">구분</th><th className="pb-1 font-medium">종목</th>
            <th className="pb-1 font-medium">방향</th>
            <th className="pb-1 text-right font-medium">{fill ? "체결가" : "방식 · 가격"}</th>
            <th className="pb-1 text-right font-medium">수량</th>
            <th className="pb-1 text-right font-medium">금액</th>
          </tr></thead>
          <tbody>
            {orders.map((o, i) => (
              <tr key={i} className="border-t border-line/40">
                <td className="py-1.5 text-muted">{KIND_KO_J[o.kind] ?? o.kind}</td>
                <td className="py-1.5">{o.instrument === "K200" ? "200 ETF" : "레버리지"}</td>
                <td className={`py-1.5 font-semibold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                <td className="table-num py-1.5">
                {fill
                  ? (o.price ? fpx(o.price) : "—")
                  : o.price
                    ? <><span className="mr-1 rounded bg-raised px-1 py-0.5 text-[10.5px] font-bold text-muted">지정가</span>{fpx(o.price)}</>
                    : <><span className="mr-1 rounded bg-accent/15 px-1 py-0.5 text-[10.5px] font-bold text-accent">시장가</span></>}
              </td>
                <td className="table-num py-1.5">{o.qty.toLocaleString()}</td>
                <td className="table-num py-1.5 text-muted">{o.price ? fm(o.price * o.qty) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
