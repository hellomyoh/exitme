"use client";

/** 백테스트 3스텝 위저드 — 조건 → 실행(WS 진행률) → 결과 (feature-backtest §9). */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AreaSeries, createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { Badge, Callout, Card, CardTitle, fmtPct, GaugeBar, PageTitle, Stat } from "../../components/ui";

type Flags = Record<string, boolean>;
type Kpi = { total_return: number; cagr: number | null; mdd: number; sharpe: number | null; trades: number; win_rate: number | null; profit_factor: number | null };
type EquityPoint = { date: string; equity: number; benchmark: number; regime: string; exposure: number };
type Job = { id: number; status: string; progress: number; params: Record<string, unknown>; kpi: Kpi | null; equity?: EquityPoint[]; trades?: Record<string, unknown>[]; stale?: boolean };
type JournalOrder = { instrument: string; side: string; otype?: string; kind: string; price: number | null; qty: number };
type JournalDay = {
  date: string; regime: string; exposure: number; equity: number; day_return: number; total_return: number;
  cash: number; qty_200: number; qty_lev: number; planned: JournalOrder[]; fills: JournalOrder[];
};

const FLAG_LABELS: [string, string, string][] = [
  ["f1_no_tp_in_bull", "① 상승장 익절 제거", "체결분을 코어로 보유 — 기여도 최대"],
  ["f2_downside_vol", "② 하방 변동성 타게팅", "상승 변동성에 벌점 없음"],
  ["f3_fast_regime", "③ 레짐 판정 단축", "MA20>MA60 교차 (끄면 v1 기울기)"],
  ["f4_leverage", "④ 레버리지 모듈", "Emax 1.30 · E>1 초과분만"],
  ["f5_gap_filter", "⑤ 갭 필터 + 잔여예산", "갭 하락 방어 · 예산 초과 미발주"],
];
const ETF_INFO: Record<string, { label: string; fee: number }> = {
  KODEX: { label: "KODEX 200", fee: 0.0015 },
  TIGER: { label: "TIGER 200", fee: 0.0005 },
};

export default function SimulatorPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [etf, setEtf] = useState<"KODEX" | "TIGER">("KODEX");
  const [capital, setCapital] = useState("100000000");
  const [dateFrom, setDateFrom] = useState("2017-01-02");
  const [dateTo, setDateTo] = useState(new Date().toISOString().slice(0, 10));
  const [flags, setFlags] = useState<Flags>(Object.fromEntries(FLAG_LABELS.map(([k]) => [k, true])));
  const [jobId, setJobId] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<Job[]>([]);
  const [overlay, setOverlay] = useState<number[]>([]);
  const [journal, setJournal] = useState<JournalDay[] | null>(null);
  const [journalBusy, setJournalBusy] = useState(false);
  const [tradedOnly, setTradedOnly] = useState(true);
  const [visibleDays, setVisibleDays] = useState(30);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);

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

  async function loadHistory() {
    const res = await apiFetch("/backtests");
    if (res.ok) setHistory(((await res.json()) as { items: Job[] }).items.filter((j) => j.status === "DONE"));
  }

  async function start() {
    setError("");
    const res = await apiFetch("/backtests", {
      method: "POST",
      body: JSON.stringify({
        capital: Number(capital), date_from: dateFrom, date_to: dateTo, etf, flags,
        costs: { fee_200: ETF_INFO[etf].fee },
      }),
    });
    if (!res.ok) {
      setError(((await res.json()) as { detail?: string }).detail ?? `실행 실패 (${res.status})`);
      return;
    }
    const { id } = (await res.json()) as { id: number };
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

  function drawEquity(main: Job, others: Job[]) {
    if (!chartRef.current || !main.equity) return;
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
    chart.addSeries(LineSeries, { color: "#b45309", lineWidth: 2, title: "전략" }).setData(norm(main.equity, "equity"));
    // 종목 추세(매수보유)는 하단 서브페인으로 분리 — 수익 곡선을 가리지 않도록 (2026-08-28 지시)
    chart.addSeries(AreaSeries, {
      lineColor: "#64748b", lineWidth: 1, title: "종목 추세",
      topColor: "rgba(100,116,139,0.14)", bottomColor: "rgba(100,116,139,0.0)",
      priceLineVisible: false,
    }, 1).setData(norm(main.equity, "benchmark"));
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
    setCapital(String(p.capital)); setDateFrom(p.date_from); setDateTo(p.date_to);
    if (p.etf === "KODEX" || p.etf === "TIGER") setEtf(p.etf);
    if (p.flags) setFlags(p.flags);
    setStep(1);
  }

  const kpi = job?.kpi;

  return (
    <main>
      <PageTitle title="시뮬레이터" sub="RAVG v2 백테스트 — 조건 설정 → 실행 → 결과. 모의 계산이며 투자 권유가 아닙니다." />

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
              {(Object.keys(ETF_INFO) as ("KODEX" | "TIGER")[]).map((k) => (
                <button key={k} onClick={() => setEtf(k)}
                  className={`rounded-xl border p-4 text-left transition-colors ${etf === k ? "border-accent bg-accent-dim" : "border-line bg-inset hover:border-line-strong"}`}>
                  <div className="font-bold">{ETF_INFO[k].label}</div>
                  <div className="mt-0.5 text-xs text-faint">총보수 연 {(ETF_INFO[k].fee * 100).toFixed(2)}% · 레버리지는 KODEX 공통</div>
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
              <label className="grid gap-1 text-xs text-faint">자본금(원)
                <input className="input" value={capital} onChange={(e) => setCapital(e.target.value)} /></label>
            </div>
          </Card>
          <Card>
            <CardTitle>절제(Ablation) 플래그 <span className="normal-case text-faint">· 하나씩 꺼서 모듈 기여 검증</span></CardTitle>
            <div className="grid gap-1.5">
              {FLAG_LABELS.map(([key, label, desc]) => (
                <label key={key} className={`flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 transition-colors hover:bg-raised ${
                  key !== "f4_leverage" || flags.f4_leverage ? "" : "opacity-60"}`}>
                  <span className="flex items-center gap-3">
                    <input type="checkbox" className="accent-[#b45309]" checked={flags[key]}
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
                {history.slice(0, 8).map((j) => (
                  <div key={j.id} className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-[13px] hover:bg-raised">
                    <span className="w-10 text-faint">#{j.id}</span>
                    <Badge tone="default">{(j.params as { etf?: string }).etf ?? "KODEX"}</Badge>
                    <span className={`w-20 text-right font-semibold ${(j.kpi?.total_return ?? 0) >= 0 ? "text-up" : "text-down"}`}>
                      {fmtPct(j.kpi?.total_return)}
                    </span>
                    <span className="ml-auto flex gap-1.5">
                      <button className="btn !px-2.5 !py-1 text-xs" onClick={() => void showResult(j.id)}>보기</button>
                      <button className="btn-ghost btn !px-2.5 !py-1 text-xs" onClick={() => clone(j)}>복제</button>
                    </span>
                  </div>
                ))}
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
          {job.stale && <Callout icon="⚠️">시세 데이터가 갱신되었습니다(stale) — 재실행을 권장합니다.</Callout>}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
            <Stat label="총수익률" value={fmtPct(kpi?.total_return)} tone={(kpi?.total_return ?? 0) >= 0 ? "up" : "down"} />
            <Stat label="CAGR" value={kpi?.cagr === null ? "1년 미만" : fmtPct(kpi?.cagr)} />
            <Stat label="MDD" value={fmtPct(kpi?.mdd)} tone="down" />
            <Stat label="샤프" value={kpi?.sharpe?.toFixed(2) ?? "—"} />
            <Stat label="거래" value={String(kpi?.trades ?? 0)} hint="FIFO 라운드트립" />
            <Stat label="승률" value={fmtPct(kpi?.win_rate)} />
            <Stat label="손익비" value={kpi?.profit_factor?.toFixed(2) ?? "—"} />
          </div>
          <Card>
            <CardTitle right={
              <span className="flex items-center gap-2 text-xs text-faint">
                <i className="inline-block h-0.5 w-4 bg-accent" />전략 수익 (상단)
                <i className="inline-block h-0.5 w-4 bg-[#64748b]" />종목 추세 (하단)
              </span>
            }>자산곡선 <span className="normal-case text-faint">· 초기자본 = 100 정규화</span></CardTitle>
            <div ref={chartRef} className="h-96" />
          </Card>
          <Card>
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn" onClick={downloadCsv}>거래내역 CSV</button>
              <button className="btn" onClick={() => clone(job)}>이 조건으로 다시 설정</button>
              <button className="btn btn-primary" onClick={() => void (async () => {
                const r = await apiFetch(`/portfolios/from-backtest/${job.id}`, { method: "POST" });
                if (r.ok) router.push("/portfolio");
              })()}>실전매매로 전환 →</button>
              <span className="ml-auto flex flex-wrap items-center gap-2 text-[13px] text-muted">
                오버레이:
                {history.filter((h) => h.id !== job.id).slice(0, 8).map((h) => (
                  <label key={h.id} className="flex cursor-pointer items-center gap-1">
                    <input type="checkbox" className="accent-[#b45309]" checked={overlay.includes(h.id)}
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
                <input type="checkbox" className="accent-[#b45309]" checked={tradedOnly}
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
                        <span className="hidden text-[13px] text-faint sm:inline">누적 {(d.total_return * 100).toFixed(1)}%</span>
                        <span className="hidden text-[13px] text-muted md:inline">평가 {Math.round(d.equity).toLocaleString()}원</span>
                        <span className="hidden text-[13px] text-faint lg:inline">보유 200ETF {d.qty_200.toLocaleString()} · 레버 {d.qty_lev.toLocaleString()}</span>
                        <span className="ml-auto text-[13px] text-faint">주문 {d.planned.length} · 체결 {d.fills.length}</span>
                      </summary>
                      <div className="grid gap-4 border-t border-line px-4 py-4 lg:grid-cols-2">
                        <JournalOrders title="장 시작 전 주문표 (계획)" orders={d.planned} fill={false} />
                        <JournalOrders title="체결 내역" orders={d.fills} fill={true} />
                        <div className="col-span-full flex flex-wrap gap-x-6 gap-y-1 text-[13.5px] text-muted">
                          <span>노출 E <b className="text-ink">{(d.exposure * 100).toFixed(1)}%</b></span>
                          <span>현금 <b className="text-ink">{d.cash.toLocaleString()}원</b></span>
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

function JournalOrders({ title, orders, fill }: { title: string; orders: JournalOrder[]; fill: boolean }) {
  return (
    <div>
      <div className="mb-1.5 text-[13px] font-semibold text-muted">{title} ({orders.length}건)</div>
      {orders.length === 0 ? <p className="text-[13px] text-faint">없음</p> : (
        <table className="w-full text-[14px]">
          <thead><tr className="text-left text-xs text-faint">
            <th className="pb-1 font-medium">구분</th><th className="pb-1 font-medium">종목</th>
            <th className="pb-1 font-medium">방향</th>
            <th className="pb-1 text-right font-medium">{fill ? "체결가" : "지정가"}</th>
            <th className="pb-1 text-right font-medium">수량</th>
            <th className="pb-1 text-right font-medium">금액</th>
          </tr></thead>
          <tbody>
            {orders.map((o, i) => (
              <tr key={i} className="border-t border-line/40">
                <td className="py-1.5 text-muted">{KIND_KO_J[o.kind] ?? o.kind}</td>
                <td className="py-1.5">{o.instrument === "K200" ? "200 ETF" : "레버리지"}</td>
                <td className={`py-1.5 font-semibold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                <td className="table-num py-1.5">{o.price ? o.price.toLocaleString() : "시가"}</td>
                <td className="table-num py-1.5">{o.qty.toLocaleString()}</td>
                <td className="table-num py-1.5 text-muted">{o.price ? (o.price * o.qty).toLocaleString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
