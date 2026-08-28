"use client";

/**
 * 백테스트 3스텝 위저드 (feature-backtest §9): 조건 → 실행(WS 진행률·취소) → 결과.
 * 오버레이 비교(최대 5개, 초기자본=100 정규화), stale 배지, 파라미터 복제 재실행.
 */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, hasToken } from "../../lib/api";

type Flags = Record<string, boolean>;
type Kpi = { total_return: number; cagr: number | null; mdd: number; sharpe: number | null; trades: number; win_rate: number | null; profit_factor: number | null };
type EquityPoint = { date: string; equity: number; benchmark: number; regime: string; exposure: number };
type Job = { id: number; status: string; progress: number; params: Record<string, unknown>; kpi: Kpi | null; equity?: EquityPoint[]; trades?: Record<string, unknown>[]; stale?: boolean };

const FLAG_LABELS: [string, string][] = [
  ["f1_no_tp_in_bull", "① 상승장 익절 제거"],
  ["f2_downside_vol", "② 하방 변동성 타게팅"],
  ["f3_fast_regime", "③ 레짐 판정 단축 (MA20>MA60)"],
  ["f4_leverage", "④ 레버리지 모듈 (Emax 1.30)"],
  ["f5_gap_filter", "⑤ 갭 필터 + 잔여예산"],
];

const box = { background: "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "8px 10px" } as const;
const pct = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`);

export default function SimulatorPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
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
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApi = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!hasToken()) router.push("/login");
    void loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadHistory() {
    const res = await apiFetch("/backtests");
    if (res.ok) setHistory(((await res.json()) as { items: Job[] }).items.filter((j) => j.status === "DONE"));
  }

  async function start() {
    setError("");
    const res = await apiFetch("/backtests", {
      method: "POST",
      body: JSON.stringify({ capital: Number(capital), date_from: dateFrom, date_to: dateTo, flags }),
    });
    if (!res.ok) {
      const detail = (await res.json()) as { detail?: string };
      setError(detail.detail ?? `실행 실패 (${res.status}) — 시세 시딩 여부를 확인하세요`);
      return;
    }
    const { id } = (await res.json()) as { id: number };
    setJobId(id);
    setProgress(0);
    setStep(2);
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/backtests/${id}`);
    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data as string) as { status: string; progress?: number; error?: string };
      if (msg.progress !== undefined) setProgress(msg.progress);
      if (msg.status === "DONE") { ws.close(); await showResult(id); }
      if (msg.status === "FAILED") { ws.close(); setError(msg.error ?? "실행 실패"); setStep(1); }
      if (msg.status === "CANCELED") { ws.close(); setStep(1); }
    };
  }

  async function cancel() {
    if (jobId) await apiFetch(`/backtests/${jobId}/cancel`, { method: "POST" });
  }

  async function showResult(id: number) {
    const res = await apiFetch(`/backtests/${id}`);
    if (!res.ok) return;
    const j = (await res.json()) as Job;
    setJob(j);
    setStep(3);
    void loadHistory();
    setTimeout(() => drawEquity(j, []), 50);
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
    chartApi.current?.remove();
    const chart = createChart(chartRef.current, {
      layout: { background: { color: "#111117" }, textColor: "#c9c9d1", attributionLogo: false },
      grid: { vertLines: { color: "#22222c" }, horzLines: { color: "#22222c" } },
      autoSize: true,
    });
    chartApi.current = chart;
    const norm = (pts: EquityPoint[], key: "equity" | "benchmark") => {
      const base = pts[0][key];
      return pts.map((p) => ({ time: p.date, value: (p[key] / base) * 100 }));
    };
    chart.addSeries(LineSeries, { color: "#e8b339", lineWidth: 2, title: "전략" }).setData(norm(main.equity, "equity"));
    chart.addSeries(LineSeries, { color: "#8888a0", lineWidth: 1, title: "KODEX 200 매수보유" }).setData(norm(main.equity, "benchmark"));
    const colors = ["#4fc3f7", "#ab7df8", "#66d9a8", "#f2617a"];
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
    const p = j.params as { capital: number; date_from: string; date_to: string; flags?: Flags };
    setCapital(String(p.capital));
    setDateFrom(p.date_from);
    setDateTo(p.date_to);
    if (p.flags) setFlags(p.flags);
    setStep(1);
  }

  return (
    <main style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, minHeight: "100vh" }}>
      <h1 style={{ fontSize: "1.3rem" }}>시뮬레이터 — RAVG v2 <span style={{ opacity: 0.5, fontSize: 13 }}>Step {step}/3 · 모의 계산이며 투자 권유가 아닙니다</span></h1>

      {step === 1 && (
        <section style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 520 }}>
          <label>자본금(원) <input style={{ ...box, width: "100%" }} value={capital} onChange={(e) => setCapital(e.target.value)} /></label>
          <div style={{ display: "flex", gap: 8 }}>
            <label style={{ flex: 1 }}>시작일 <input type="date" style={{ ...box, width: "100%" }} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
            <label style={{ flex: 1 }}>종료일 <input type="date" style={{ ...box, width: "100%" }} value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
          </div>
          <fieldset style={{ ...box, display: "flex", flexDirection: "column", gap: 4 }}>
            <legend>절제(ablation) 플래그 — 하나씩 꺼서 모듈 기여를 검증 (정본 §11 순서)</legend>
            {FLAG_LABELS.map(([key, label]) => (
              <label key={key} style={{ opacity: key !== "f4_leverage" || flags.f4_leverage ? 1 : 0.6 }}>
                <input type="checkbox" checked={flags[key]} onChange={(e) => setFlags({ ...flags, [key]: e.target.checked })} /> {label}
              </label>
            ))}
            {!flags.f4_leverage && <small style={{ opacity: 0.6 }}>④ off — 레버리지 2트랙·강제청산 규칙 비활성 (E ≤ 1.0)</small>}
          </fieldset>
          <small style={{ opacity: 0.6 }}>비용 기본값: 수수료 0.015% · 시장가 슬리피지 0.1%(지정가 0) · 레버리지 과세 15.4%(단순화) · 보수 일할</small>
          {error && <p style={{ color: "#f2617a" }}>{error}</p>}
          <button style={{ ...box, cursor: "pointer" }} onClick={() => void start()}>실행 →</button>
          {history.length > 0 && (
            <details>
              <summary style={{ cursor: "pointer", opacity: 0.8 }}>지난 결과 ({history.length}) — 복제 재실행 / 결과 보기</summary>
              {history.slice(0, 10).map((j) => (
                <div key={j.id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0" }}>
                  <span style={{ opacity: 0.7 }}>#{j.id}</span>
                  <span>{pct(j.kpi?.total_return)}</span>
                  <button style={{ ...box, padding: "2px 8px", cursor: "pointer" }} onClick={() => void showResult(j.id)}>보기</button>
                  <button style={{ ...box, padding: "2px 8px", cursor: "pointer" }} onClick={() => clone(j)}>복제</button>
                </div>
              ))}
            </details>
          )}
        </section>
      )}

      {step === 2 && (
        <section style={{ maxWidth: 520 }}>
          <p>백테스트 실행 중… {progress}%</p>
          <div style={{ background: "#22222c", borderRadius: 6, height: 10 }}>
            <div style={{ width: `${progress}%`, background: "#e8b339", height: 10, borderRadius: 6, transition: "width .3s" }} />
          </div>
          <button style={{ ...box, marginTop: 12, cursor: "pointer" }} onClick={() => void cancel()}>취소</button>
        </section>
      )}

      {step === 3 && job && (
        <section style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1 }}>
          {job.stale && <p style={{ color: "#e8b339" }}>⚠ 시세 데이터가 갱신되었습니다(stale) — 재실행을 권장합니다. 오버레이 비교가 제한됩니다.</p>}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(130px,1fr))", gap: 8, fontVariantNumeric: "tabular-nums" }}>
            {[["총수익률", pct(job.kpi?.total_return)], ["CAGR", job.kpi?.cagr === null ? "1년 미만" : pct(job.kpi?.cagr)],
              ["MDD", pct(job.kpi?.mdd)], ["샤프", job.kpi?.sharpe?.toFixed(2) ?? "—"],
              ["거래(라운드트립)", String(job.kpi?.trades ?? 0)], ["승률", pct(job.kpi?.win_rate, 1)],
              ["손익비", job.kpi?.profit_factor?.toFixed(2) ?? "—"]].map(([k, v]) => (
              <div key={k} style={{ ...box }}><div style={{ opacity: 0.6, fontSize: 12 }}>{k}</div><div style={{ fontSize: 18 }}>{v}</div></div>
            ))}
          </div>
          <div ref={chartRef} style={{ flex: 1, minHeight: 300 }} />
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button style={{ ...box, cursor: "pointer" }} onClick={downloadCsv}>거래내역 CSV</button>
            <button style={{ ...box, cursor: "pointer" }} onClick={() => clone(job)}>이 조건으로 다시 설정</button>
            <button style={{ ...box, cursor: "pointer" }} onClick={() => void (async () => {
              const r = await apiFetch(`/portfolios/from-backtest/${job.id}`, { method: "POST" });
              if (r.ok) router.push("/portfolio");
            })()}>실전매매로 전환 →</button>
            <span style={{ opacity: 0.7 }}>오버레이(최대 4개 추가):</span>
            {history.filter((h) => h.id !== job.id).slice(0, 8).map((h) => (
              <label key={h.id} style={{ opacity: 0.85 }}>
                <input type="checkbox" checked={overlay.includes(h.id)} disabled={!overlay.includes(h.id) && overlay.length >= 4}
                  onChange={(e) => {
                    const next = e.target.checked ? [...overlay, h.id] : overlay.filter((x) => x !== h.id);
                    setOverlay(next);
                    void drawOverlay(next);
                  }} /> #{h.id}
              </label>
            ))}
          </div>
          <p style={{ opacity: 0.45, fontSize: 12 }}>자산곡선은 초기자본=100 정규화. 벤치마크 = KODEX 200 매수보유(보수 반영). 세금은 단순화 계산.</p>
        </section>
      )}
    </main>
  );
}
