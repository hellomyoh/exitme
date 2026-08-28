"use client";

/** 실전매매 기록 — 수익률 카드 + 거래 등록 (feature-portfolio §9). */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { Badge, Card, CardTitle, EmptyState, fmtPct, fmtWon, GaugeBar, PageTitle, pnlTone, Stat, Tip } from "../../components/ui";

type Position = {
  code: string; name: string; qty: number; avg_price: number; price: number; value: number;
  return: number; unrealized: number; held_days: number; annualized: number | null;
  best_return: number; worst_return: number; target_price: number | null; stop_price: number | null;
};
type Summary = {
  portfolio: { id: number; name: string; kind: string; backtest_id: number | null };
  as_of: string | null; cash: number; stock_value: number; total_equity: number;
  realized_pnl: number; unrealized_pnl: number; estimated_costs: number;
  twr: number | null; xirr: number | null; positions: Position[];
};
type PortfolioItem = { id: number; name: string; kind: string };
type Tx = {
  id: number; kind: string; code: string | null; name: string | null;
  qty: number | null; price: number | null; amount: number | null;
  realized_pnl: number | null; executed_at: string; memo: string | null;
};
type OrderRow = { instrument: string; side: string; otype: string; qty: number; price: number | null; kind: string };
type Signal = { status: string; trade_date?: string; regime?: string; e_target?: number; orders?: OrderRow[]; gap_cancel_below?: number; basis?: string; account?: { qty_200: number; qty_lev: number; cash: number } };

const TX_KO: Record<string, string> = { buy: "매수", sell: "매도", deposit: "입금", withdraw: "출금" };
const REGIME_KO2: Record<string, string> = { BULL: "상승장", NEUTRAL: "중립장", BEAR: "하락장" };
const ORDER_KIND_KO: Record<string, string> = {
  grid1: "그리드 1차", grid2: "그리드 2차", grid3: "그리드 3차", tp: "익절", reduce: "축소",
  lev_strat: "레버 전략", lev_tact1: "레버 전술1", lev_tact2: "레버 전술2", lev_tact_exit: "전술 이탈", lev_liq: "레버 청산",
};

const toneCls = { up: "text-up", down: "text-down", default: "text-ink" };

export default function PortfolioPage() {
  const router = useRouter();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [pid, setPid] = useState<number | null>(null);
  const [sum, setSum] = useState<Summary | null>(null);
  const [includeCosts, setIncludeCosts] = useState(true);
  const [form, setForm] = useState({ kind: "buy", code: "069500", qty: "", price: "", amount: "", memo: "" });
  const [msg, setMsg] = useState("");
  const [newName, setNewName] = useState("");
  const [showStart, setShowStart] = useState(false);
  const [startMode, setStartMode] = useState<"fresh" | "holdings">("fresh");
  const [startCash, setStartCash] = useState("");
  const [holdings, setHoldings] = useState<{ code: string; qty: string; price: string }[]>([
    { code: "069500", qty: "", price: "" },
  ]);
  const [txs, setTxs] = useState<Tx[]>([]);
  const [signal, setSignal] = useState<Signal | null>(null);
  const [curve, setCurve] = useState<{ date: string; equity: number; index: number }[]>([]);
  const eqRef = useRef<HTMLDivElement>(null);
  const eqApi = useRef<IChartApi | null>(null);

  const load = useCallback(async (id: number | null) => {
    let sid: number | null = id;
    const res = await apiFetch(`/portfolio/summary${id ? `?portfolio_id=${id}` : ""}`);
    if (res.ok) {
      const sm = (await res.json()) as Summary;
      setSum(sm);
      sid = sm.portfolio.id;  // 기본 계좌 포함 — 주문표를 이 포트 기준으로
    }
    const pl = await apiFetch("/portfolios");
    if (pl.ok) setPortfolios(((await pl.json()) as { items: PortfolioItem[] }).items);
    const tx = await apiFetch(`/portfolio/transactions${id ? `?portfolio_id=${id}` : ""}`);
    if (tx.ok) setTxs(((await tx.json()) as { items: Tx[] }).items);
    // 오늘의 주문표 — 선택된 실전 포트의 보유·현금 기준 (2026-08-28 검토 반영)
    const sg = await apiFetch(`/signals/daily${sid ? `?portfolio_id=${sid}` : ""}`);
    if (sg.ok) setSignal((await sg.json()) as Signal);
    const eq = await apiFetch(`/portfolio/equity${sid ? `?portfolio_id=${sid}` : ""}`);
    if (eq.ok) setCurve(((await eq.json()) as { items: { date: string; equity: number; index: number }[] }).items);
  }, []);

  useEffect(() => {
    void ensureSession().then((ok) => {
      if (!ok) { router.push("/login"); return; }
      void load(pid);
    });
  }, [pid, load, router]);

  async function submit() {
    setMsg("");
    const body: Record<string, unknown> = {
      portfolio_id: pid ?? undefined, kind: form.kind, memo: form.memo || undefined,
      executed_at: new Date().toISOString(),
    };
    if (form.kind === "buy" || form.kind === "sell") {
      body.code = form.code; body.qty = Number(form.qty); body.price = Number(form.price);
    } else {
      body.amount = Number(form.amount);
    }
    const res = await apiFetch("/positions", { method: "POST", body: JSON.stringify(body) });
    if (res.ok) {
      const out = (await res.json()) as { realized_pnl: number | null };
      setMsg(out.realized_pnl !== null ? `등록됨 — 실현손익 ${fmtWon(out.realized_pnl)}` : "등록됨");
      void load(pid);
    } else {
      setMsg(((await res.json()) as { detail?: string }).detail ?? `등록 실패 (${res.status})`);
    }
  }

  async function startPortfolio() {
    const name = newName.trim() || `실전매매 ${new Date().toISOString().slice(0, 10)}`;
    const res = await apiFetch("/portfolios", { method: "POST", body: JSON.stringify({ name }) });
    if (!res.ok) return;
    const { id } = (await res.json()) as { id: number };
    const now = new Date().toISOString();
    const cash = Number(startCash.replaceAll(",", "")) || 0;
    if (startMode === "fresh") {
      // 오늘부터 새로 시작 — 이전 기록 없음, (선택) 초기 입금만
      if (cash > 0) {
        await apiFetch("/positions", { method: "POST", body: JSON.stringify({
          portfolio_id: id, kind: "deposit", amount: cash, executed_at: now, memo: "시작 입금" }) });
      }
    } else {
      // 현재 보유분 입력하고 시작 — 평단 미입력 시 최근 종가로 등록(수익률 0% 시작, 2026-08-28 지시)
      const withPrice: { code: string; qty: number; price: number }[] = [];
      for (const h of holdings) {
        const qty = Number(h.qty);
        if (qty <= 0) continue;
        let price = Number(h.price);
        if (!price) {
          const to = new Date().toISOString().slice(0, 10);
          const from = new Date(Date.now() - 15 * 86400e3).toISOString().slice(0, 10);
          const r = await fetch(`/api/ohlcv?code=${h.code}&from=${from}&to=${to}`);
          if (r.ok) {
            const items = ((await r.json()) as { items: { close: number }[] }).items;
            if (items.length) price = items[items.length - 1].close;
          }
        }
        if (price > 0) withPrice.push({ code: h.code, qty, price });
      }
      const rows = withPrice;
      const cost = rows.reduce((a, h) => a + h.qty * h.price, 0);
      if (cash + cost > 0) {
        await apiFetch("/positions", { method: "POST", body: JSON.stringify({
          portfolio_id: id, kind: "deposit", amount: cash + cost, executed_at: now, memo: "시작 입금 (현금+보유 원가)" }) });
      }
      for (const h of rows) {
        await apiFetch("/positions", { method: "POST", body: JSON.stringify({
          portfolio_id: id, kind: "buy", code: h.code, qty: h.qty, price: h.price,
          executed_at: now, memo: "보유분 등록" }) });
      }
    }
    setShowStart(false); setNewName(""); setStartCash("");
    setHoldings([{ code: "069500", qty: "", price: "" }]);
    setPid(id);
  }

  async function deletePortfolio() {
    if (!sum) return;
    const name = sum.portfolio.name;
    if (!window.confirm(`'${name}' 실전매매를 삭제할까요?\n등록한 거래·손익 기록이 모두 삭제되며 되돌릴 수 없습니다.`)) return;
    const res = await apiFetch(`/portfolios/${sum.portfolio.id}`, { method: "DELETE" });
    if (res.ok) { setPid(null); void load(null); }
  }

  useEffect(() => {
    if (!eqRef.current) return;
    try { eqApi.current?.remove(); } catch { /* already disposed */ }
    eqApi.current = null;
    if (curve.length < 2) return;  // 1일 이하 → 차트 대신 안내 문구 (오늘 시작 케이스)
    const chart = createChart(eqRef.current, {
      layout: { background: { color: "transparent" }, textColor: "#858c9b", attributionLogo: false, fontSize: 12 },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(18,24,40,0.07)" } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
      autoSize: true,
    });
    eqApi.current = chart;
    chart.addSeries(LineSeries, {
      color: "#b45309", lineWidth: 2, title: "수익률 지수",
      // 데이터가 짧아도(시작 직후) 점이 잘 보이도록 마커 표시
      pointMarkersVisible: curve.length <= 30,
    }).setData(curve.map((c) => ({ time: c.date, value: c.index })));
    chart.timeScale().fitContent();
    return () => { try { eqApi.current?.remove(); } catch { /* noop */ } eqApi.current = null; };
  }, [curve]);

  const net = sum ? sum.unrealized_pnl + sum.realized_pnl - (includeCosts ? sum.estimated_costs : 0) : 0;

  return (
    <main>
      <PageTitle title="실전매매" sub="체결 내역을 등록해 매수 시점 기준 수익률을 추적합니다 — 지연 시세 기준" />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select className="input" value={pid ?? ""} onChange={(e) => setPid(e.target.value ? Number(e.target.value) : null)}>
          <option value="">내 계좌 (기본)</option>
          {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="btn-ghost btn !py-2 text-[14px] !text-up" onClick={() => void deletePortfolio()}>이 포트 삭제</button>
        <span className="mx-1 h-5 w-px bg-line-strong" />
        <button className="btn btn-primary !py-2" onClick={() => setShowStart(!showStart)}>＋ 새 실전매매 시작</button>
        <label className="flex items-center gap-1.5 text-[13px] text-muted">
          <input type="checkbox" className="accent-[#b45309]" checked={includeCosts} onChange={(e) => setIncludeCosts(e.target.checked)} />
          비용 포함 (추정 수수료)
        </label>
        {sum?.as_of && <span className="ml-auto text-xs text-faint">기준일 {sum.as_of} · 지연 시세</span>}
      </div>

      {showStart && (
        <Card className="mb-4 border-accent">
          <CardTitle>새 실전매매 시작</CardTitle>
          <div className="mb-4 grid gap-2 sm:grid-cols-2">
            <button onClick={() => setStartMode("fresh")}
              className={`rounded-xl border p-4 text-left transition-colors ${startMode === "fresh" ? "border-accent bg-accent-dim" : "border-line bg-inset hover:border-line-strong"}`}>
              <div className="font-bold">오늘부터 새로 시작</div>
              <div className="mt-0.5 text-[13px] text-faint">이전 거래 기록 없음 — 빈 계좌로 시작 (초기 입금 선택)</div>
            </button>
            <button onClick={() => setStartMode("holdings")}
              className={`rounded-xl border p-4 text-left transition-colors ${startMode === "holdings" ? "border-accent bg-accent-dim" : "border-line bg-inset hover:border-line-strong"}`}>
              <div className="font-bold">현재 보유분 입력하고 시작</div>
              <div className="mt-0.5 text-[13px] text-faint">이미 들고 있는 주식 수량·평단을 등록하고 이어서 관리</div>
            </button>
          </div>
          <div className="grid gap-3">
            <div className="flex flex-wrap items-end gap-3">
              <label className="grid gap-1 text-[13px] text-faint">이름
                <input className="input w-52" placeholder={`실전매매 ${new Date().toISOString().slice(0, 10)}`}
                  value={newName} onChange={(e) => setNewName(e.target.value)} /></label>
              <label className="grid gap-1 text-[13px] text-faint">{startMode === "fresh" ? "초기 입금(원, 선택)" : "보유 외 현금(원)"}
                <input className="input w-44" placeholder="예: 50000000" value={startCash}
                  onChange={(e) => setStartCash(e.target.value)} /></label>
            </div>
            {startMode === "holdings" && (
              <div className="grid gap-2">
                <div className="text-[13px] font-semibold text-muted">보유 종목 (수량 · 평균단가)</div>
                <p className="text-[12.5px] leading-relaxed text-faint">
                  💡 <b className="text-muted">실제 매입 평단</b>을 입력하면 지금까지의 수익이 반영되고,
                  <b className="text-muted"> 비워두면 오늘 종가</b>로 등록되어 <b className="text-muted">수익률 0%부터</b> 추적을 시작합니다.
                </p>
                {holdings.map((h, i) => (
                  <div key={i} className="flex flex-wrap items-center gap-2">
                    <select className="input !py-2" value={h.code}
                      onChange={(e) => setHoldings(holdings.map((x, j) => j === i ? { ...x, code: e.target.value } : x))}>
                      <option value="069500">KODEX 200</option>
                      <option value="102110">TIGER 200</option>
                      <option value="122630">KODEX 레버리지</option>
                    </select>
                    <input className="input w-28 !py-2" placeholder="수량(주)" value={h.qty}
                      onChange={(e) => setHoldings(holdings.map((x, j) => j === i ? { ...x, qty: e.target.value } : x))} />
                    <input className="input w-44 !py-2" placeholder="평단(비우면 오늘 종가)" value={h.price}
                      onChange={(e) => setHoldings(holdings.map((x, j) => j === i ? { ...x, price: e.target.value } : x))} />
                    {holdings.length > 1 && (
                      <button className="btn-ghost btn !px-2 !py-1.5 !text-up" onClick={() => setHoldings(holdings.filter((_, j) => j !== i))}>✕</button>
                    )}
                  </div>
                ))}
                <button className="btn-ghost btn w-fit !py-1.5 text-[13.5px]" onClick={() => setHoldings([...holdings, { code: "069500", qty: "", price: "" }])}>
                  ＋ 종목 추가
                </button>
              </div>
            )}
            <div className="flex gap-2">
              <button className="btn btn-primary" onClick={() => void startPortfolio()}>시작하기</button>
              <button className="btn-ghost btn" onClick={() => setShowStart(false)}>취소</button>
            </div>
            <p className="text-[12.5px] text-faint">
              💡 백테스트 결과를 그대로 이어받아 시작하려면 시뮬레이터 결과 화면의 &quot;실전매매로 전환&quot;을 사용하세요 —
              백테스트 종료 시점의 현금·보유가 자동 등록됩니다.
            </p>
          </div>
        </Card>
      )}

      {sum && (
        <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
          <Stat label="총자산" value={fmtWon(sum.total_equity)} tip="현금 + 보유 주식 평가액(최근 종가 기준)의 합" />
          <Stat label="현금" value={fmtWon(sum.cash)} tip="입금 − 출금 − 매수금액 + 매도금액의 원장 잔액" />
          <Stat label="주식" value={fmtWon(sum.stock_value)} tip="보유 수량 × 최근 종가 (지연 시세)" />
          <Stat label="실현손익" value={fmtWon(sum.realized_pnl)} tone={pnlTone(sum.realized_pnl)} tip="매도로 확정된 손익의 누적 — 매도가와 매수가(FIFO 선입선출 매칭)의 차이" />
          <Stat label="평가손익" value={fmtWon(sum.unrealized_pnl)} tone={pnlTone(sum.unrealized_pnl)} tip="아직 팔지 않은 보유분의 손익 — (현재가 − 평균단가) × 보유 수량" />
          <Stat label={`순손익${includeCosts ? " (비용차감)" : ""}`} value={fmtWon(net)} tone={pnlTone(net)} tip="실현손익 + 평가손익 − 추정 수수료(체크 시). 이 계좌의 전체 성과 금액" />
          <Stat label="TWR" value={fmtPct(sum.twr, 2)} tip="시간가중수익률 — 입출금 시점의 영향을 제거한 운용 성과. 펀드 수익률과 같은 방식이며, 입금이 많아도 왜곡되지 않습니다" />
          <Stat label="XIRR" value={fmtPct(sum.xirr, 2)} tip="내부수익률(연환산) — 입출금 현금흐름과 현재 평가액으로 계산한 '내 돈 기준' 연 수익률" />
        </div>
      )}

      {/* 수익률 추이 (2026-08-28 지시 — 시뮬레이터와 동일 스타일, TWR 지수) */}
      <Card className="mb-4">
        <CardTitle right={curve.length > 0 ? (
          <span className={`text-[15px] font-bold normal-case ${curve[curve.length - 1].index >= 100 ? "text-up" : "text-down"}`}>
            {(curve[curve.length - 1].index - 100).toFixed(2)}%
          </span>
        ) : undefined}>수익률 추이 <span className="normal-case text-faint">· 시작 = 100 · 입출금 왜곡 제거(TWR)</span></CardTitle>
        {curve.length >= 2 ? (
          <div ref={eqRef} className="h-64" />
        ) : curve.length === 1 ? (
          <div className="flex items-center gap-4 rounded-xl bg-inset px-5 py-6">
            <span className="text-3xl">🌱</span>
            <div>
              <div className="text-[16px] font-bold">오늘 시작한 실전매매입니다 — 현재 수익률 {(curve[0].index - 100).toFixed(2)}%</div>
              <div className="mt-1 text-[13.5px] text-muted">평가액 {curve[0].equity.toLocaleString()}원 · 내일 종가부터 추이 그래프가 그려집니다.</div>
            </div>
          </div>
        ) : (
          <p className="text-[14px] text-faint">거래를 등록하면 수익률 추이가 표시됩니다.</p>
        )}
      </Card>

      {/* 거래 등록 */}
      <Card className="mb-4">
        <CardTitle>거래 등록</CardTitle>
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1 text-xs text-faint">구분
            <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="buy">매수</option><option value="sell">매도</option>
              <option value="deposit">입금</option><option value="withdraw">출금</option>
            </select>
          </label>
          {(form.kind === "buy" || form.kind === "sell") ? (
            <>
              <label className="grid gap-1 text-xs text-faint">종목
                <select className="input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })}>
                  <option value="069500">KODEX 200</option>
                  <option value="102110">TIGER 200</option>
                  <option value="122630">KODEX 레버리지</option>
                </select>
              </label>
              <label className="grid gap-1 text-xs text-faint">수량
                <input className="input w-24" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} /></label>
              <label className="grid gap-1 text-xs text-faint">단가(원)
                <input className="input w-32" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
            </>
          ) : (
            <label className="grid gap-1 text-xs text-faint">금액(원)
              <input className="input w-40" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
          )}
          <label className="grid gap-1 text-xs text-faint">메모
            <input className="input w-44" value={form.memo} onChange={(e) => setForm({ ...form, memo: e.target.value })} /></label>
          <button className="btn btn-primary" onClick={() => void submit()}>등록</button>
          {msg && <span className="text-[13px] text-muted">{msg}</span>}
        </div>
      </Card>

      {/* 오늘의 주문표 (2026-08-28 지시 — 실전매매 중간 섹션) */}
      <Card className="mb-4">
        <CardTitle right={<a href="/signals" className="text-[13.5px] font-semibold normal-case text-accent">전체 주문표 →</a>}>
          오늘의 주문표 {signal?.status === "OK" && (
            <span className="normal-case text-faint">· {signal.trade_date} 종가 · {REGIME_KO2[signal.regime ?? ""]} · E {fmtPct(signal.e_target)}
              {signal.basis === "portfolio" ? " · 이 포트 보유·현금 기준" : " · 모델 기준"}</span>
          )}
        </CardTitle>
        {signal?.status === "OK" && signal.orders && signal.orders.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[14.5px]">
              <thead><tr className="border-b border-line text-left text-[13px] text-faint">
                <th className="pb-2 font-medium">구분</th><th className="pb-2 font-medium">종목</th>
                <th className="pb-2 font-medium">방향</th>
                <th className="pb-2 text-right font-medium">지정가</th>
                <th className="pb-2 text-right font-medium">수량{signal?.basis === "portfolio" ? " (내 계좌 기준)" : " (모델 1억)"}</th>
              </tr></thead>
              <tbody>
                {signal.orders.map((o, i) => (
                  <tr key={i} className="border-b border-line/50 last:border-0">
                    <td className="py-2"><Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{ORDER_KIND_KO[o.kind] ?? o.kind}</Badge></td>
                    <td className="py-2">{o.instrument === "K200" ? "KODEX 200" : "KODEX 레버리지"}</td>
                    <td className={`py-2 font-bold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                    <td className="table-num py-2 font-semibold">{o.price ? o.price.toLocaleString() : "시가"}</td>
                    <td className="table-num py-2">{o.qty.toLocaleString()}주</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {signal.gap_cancel_below && (
              <p className="mt-2 text-[13px] text-faint">⚠️ 시가 {signal.gap_cancel_below.toLocaleString()}원 이하 출발 시 그리드 전량 취소 — 상세는 전체 주문표 참조</p>
            )}
          </div>
        ) : (
          <p className="text-[14px] text-faint">
            {signal?.status === "OK" ? "오늘은 신규 주문이 없습니다." : "시그널이 아직 없습니다 — 장 마감 배치(16:05) 이후 표시됩니다."}
          </p>
        )}
      </Card>

      {/* 포지션 카드 */}
      {sum?.positions.length === 0 ? (
        <EmptyState icon="📒" title="보유 포지션이 없습니다"
          desc="HTS에서 체결한 매수 내역을 위 폼으로 등록하면 수익률 추적이 시작됩니다. 입출금도 등록해야 TWR·XIRR이 정확해집니다." />
      ) : (
        <div className="grid gap-3">
          {sum?.positions.map((p) => {
            const t = pnlTone(p.return);
            const hasBand = p.target_price && p.stop_price && p.target_price > p.stop_price;
            const ratio = hasBand ? (p.price - p.stop_price!) / (p.target_price! - p.stop_price!) : 0;
            return (
              <Card key={p.code}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <b className="text-[17px]">{p.name}</b>
                    <span className="text-xs text-faint">{p.code}</span>
                    <Badge tone="default">{p.held_days}일 보유</Badge>
                  </div>
                  <div className={`text-lg font-extrabold ${toneCls[t]}`}>
                    {fmtPct(p.return, 2)} <span className="text-[13px] font-semibold">({fmtWon(p.unrealized)})</span>
                  </div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-[14.5px] text-muted sm:grid-cols-4 lg:grid-cols-6">
                  <span>보유 <b className="text-ink">{p.qty.toLocaleString()}주</b></span>
                  <span>평단 <b className="text-ink">{fmtWon(p.avg_price)}</b></span>
                  <span>현재가 <b className="text-ink">{fmtWon(p.price)}</b></span>
                  <span>평가액 <b className="text-ink">{fmtWon(p.value)}</b></span>
                  <span>연환산 <b className="text-ink">{p.annualized === null ? "— (30일 미만)" : fmtPct(p.annualized)}</b></span>
                  <span>최고/최저 <b className="text-up">{fmtPct(p.best_return)}</b> / <b className="text-down">{fmtPct(p.worst_return)}</b></span>
                </div>
                {hasBand && (
                  <div className="mt-3">
                    <div className="mb-1 flex justify-between text-[11px] text-faint">
                      <span>손절 {fmtWon(p.stop_price!)}</span>
                      <span>목표 {fmtWon(p.target_price!)}</span>
                    </div>
                    <GaugeBar ratio={ratio} color={t === "down" ? "var(--color-down)" : "var(--color-up)"} height={6} />
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {/* 날짜별 거래 내역 (2026-08-28 지시 — 시뮬레이터 저널과 동일 UX, 기본 닫힘) */}
      {txs.length > 0 && (() => {
        const byDate = new Map<string, Tx[]>();
        for (const t of txs) {
          const d = t.executed_at.slice(0, 10);
          if (!byDate.has(d)) byDate.set(d, []);
          byDate.get(d)!.push(t);
        }
        const days = Array.from(byDate.entries());
        return (
          <Card className="mt-4">
            <CardTitle>날짜별 거래 내역</CardTitle>
            <div className="grid gap-1.5">
              {days.map(([d, list]) => {
                const realized = list.reduce((a, t) => a + (t.realized_pnl ?? 0), 0);
                const hasSell = list.some((t) => t.kind === "sell");
                return (
                  <details key={d} className="rounded-xl border border-line bg-inset">
                    <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-4 py-3 text-[14.5px] transition-colors hover:bg-raised/60">
                      <b className="w-24">{d}</b>
                      <span className="text-[13.5px] text-muted">거래 {list.length}건</span>
                      {hasSell && (
                        <span className={`text-[13.5px] font-bold ${realized > 0 ? "text-up" : realized < 0 ? "text-down" : "text-muted"}`}>
                          당일 실현손익 {realized >= 0 ? "+" : ""}{realized.toLocaleString()}원
                        </span>
                      )}
                      <span className="ml-auto text-[13px] text-faint">펼치기</span>
                    </summary>
                    <div className="overflow-x-auto border-t-2 border-line-strong px-4 py-3">
                      <table className="w-full text-[14px]">
                        <thead><tr className="text-left text-xs text-faint">
                          <th className="pb-1 font-medium">시각</th><th className="pb-1 font-medium">구분</th>
                          <th className="pb-1 font-medium">종목</th>
                          <th className="pb-1 text-right font-medium">단가/금액</th>
                          <th className="pb-1 text-right font-medium">수량</th>
                          <th className="pb-1 text-right font-medium">실현손익</th>
                          <th className="pb-1 pl-3 font-medium">메모</th>
                        </tr></thead>
                        <tbody>
                          {list.map((t) => (
                            <tr key={t.id} className="border-t border-line/40">
                              <td className="py-1.5 text-faint">{t.executed_at.slice(11, 16)}</td>
                              <td className={`py-1.5 font-semibold ${t.kind === "buy" ? "text-up" : t.kind === "sell" ? "text-down" : "text-muted"}`}>{TX_KO[t.kind]}</td>
                              <td className="py-1.5">{t.name ?? "—"}</td>
                              <td className="table-num py-1.5">{(t.price ?? t.amount ?? 0).toLocaleString()}원</td>
                              <td className="table-num py-1.5">{t.qty ? `${t.qty.toLocaleString()}주` : "—"}</td>
                              <td className={`table-num py-1.5 font-semibold ${!t.realized_pnl ? "text-faint" : t.realized_pnl > 0 ? "text-up" : "text-down"}`}>
                                {t.realized_pnl !== null && t.realized_pnl !== undefined ? `${t.realized_pnl >= 0 ? "+" : ""}${t.realized_pnl.toLocaleString()}원` : "—"}
                              </td>
                              <td className="py-1.5 pl-3 text-[13px] text-faint">{t.memo ?? ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                );
              })}
            </div>
          </Card>
        );
      })()}
    </main>
  );
}
