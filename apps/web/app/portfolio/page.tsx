"use client";

/** 실전매매 — 현황·보유·수익률 + 오늘의 주문표(체결 등록) + 일자별 매매 일지 (feature-portfolio §9, 2026-08-29 개편). */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { fmtMoneyM, fmtPriceM, MARKET_CODES, MARKET_LABEL, marketOf, priceToApi } from "../../lib/market";
import { Badge, Card, CardTitle, EmptyState, fmtPct, GaugeBar, PageTitle, pnlTone, Stat, Tip } from "../../components/ui";

type Position = {
  code: string; name: string; qty: number; avg_price: number; price: number; value: number;
  return: number; unrealized: number; held_days: number; annualized: number | null;
  best_return: number; worst_return: number; target_price: number | null; stop_price: number | null;
};
type Summary = {
  portfolio: { id: number; name: string; kind: string; backtest_id: number | null };
  as_of: string | null; cash: number; stock_value: number; total_equity: number;
  realized_pnl: number; unrealized_pnl: number; estimated_costs: number;
  principal: number; invested_cost: number;
  net_pnl: number; net_pnl_pct: number | null; unrealized_pnl_pct: number | null;
  twr: number | null; xirr: number | null; positions: Position[];
};
type PortfolioItem = { id: number; name: string; kind: string; market?: string; color?: string | null };

// 탭 배경색 프리셋 — 라이트·다크 모두에서 20% 틴트로 사용 (2026-09-05 지시)
const TAB_COLORS = ["#b45309", "#2563eb", "#059669", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#64748b"];
type OrderRow = { instrument: string; side: string; otype: string; qty: number; price: number | null; kind: string };
type JournalFill = {
  id: number; kind: string; code: string | null; name: string | null; qty: number | null;
  price: number | null; amount: number | null; realized_pnl: number | null; time: string; memo: string | null;
};
type JournalItem = {
  date: string; regime: string | null; planned: OrderRow[] | null; gap_cancel_below: number | null;
  fills: JournalFill[]; realized_pnl: number; day_return: number | null; equity: number | null;
  account: { cash: number; qty_200: number; qty_lev: number; equity: number } | null;
  e_target: number | null;
};
type Signal = { status: string; exec_day?: string; trade_date?: string; regime?: string; e_target?: number; orders?: OrderRow[]; gap_cancel_below?: number; basis?: string; name_200?: string; code_200?: string; account?: { qty_200: number; qty_lev: number; cash: number }; algo_source?: "portfolio" | "settings"; algo_overrides?: Record<string, number>; algo_detail?: { key: string; label: string; value: number; default: number | null }[] };

const TX_KO: Record<string, string> = { buy: "매수", sell: "매도", deposit: "입금", withdraw: "출금" };
const REGIME_KO2: Record<string, string> = { BULL: "상승장", NEUTRAL: "중립장", BEAR: "하락장" };
const ORDER_KIND_KO: Record<string, string> = {
  grid1: "그리드 1차", grid2: "그리드 2차", grid3: "그리드 3차", tp: "익절", reduce: "축소",
  lev_strat: "레버 전략", lev_tact1: "레버 전술1", lev_tact2: "레버 전술2", lev_tact_exit: "전술 이탈", lev_liq: "레버 청산",
  tf_entry: "추세 진입", tf_exit: "추세 이탈",
};

const toneCls = { up: "text-up", down: "text-down", default: "text-ink" };

export default function PortfolioPageWrapper() {
  return <Suspense fallback={null}><MarketKeyed /></Suspense>;
}

function MarketKeyed() {
  // 마켓 전환 시 페이지 상태 전체 리셋 — 이전 마켓의 결과·선택이 남는 것 방지 (2026-08-31 검토)
  const sp = useSearchParams();
  const resetKey = sp?.get("r") ?? "";
  return <PortfolioPage key={`${marketOf(sp)}-${resetKey}`} />;
}

function PortfolioPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const market = marketOf(sp);
  const fm = (v: number) => fmtMoneyM(market, v);
  const fpx = (v: number) => fmtPriceM(market, v);
  const unit = market === "US" ? "$" : "원";
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  // 초기 선택: ?pid= 쿼리(시뮬 전환 직후 새 포트) — 없으면 서버 기본 (2026-09-05)
  const [pid, setPid] = useState<number | null>(() => {
    const q = sp?.get("pid");
    return q ? Number(q) : null;
  });
  const [sum, setSum] = useState<Summary | null>(null);
  const [includeCosts, setIncludeCosts] = useState(true);
  const [form, setForm] = useState({ kind: "buy", code: market === "US" ? "QQQ" : "069500", qty: "", price: "", amount: "", memo: "",
    date: new Date().toISOString().slice(0, 10) });
  const [msg, setMsg] = useState("");
  const [txDays, setTxDays] = useState(15);  // 거래 내역 기본 표시 일수 — 무한 나열 방지 (2026-08-29 검토)
  const [newName, setNewName] = useState("");
  const [showStart, setShowStart] = useState(false);
  const [startMode, setStartMode] = useState<"fresh" | "holdings">("fresh");
  const [startCode200, setStartCode200] = useState("102110");  // KR 주력 조합 — 기본 TIGER (보수 연 0.05%, 2026-09-01 지시)
  const [startCash, setStartCash] = useState("");
  const [holdings, setHoldings] = useState<{ code: string; qty: string; price: string }[]>([
    { code: market === "US" ? "QQQ" : "102110", qty: "", price: "" },
  ]);
  const [journal, setJournal] = useState<JournalItem[]>([]);
  // 포트 이름·탭 배경색 편집 패널 (2026-09-05 지시)
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editColor, setEditColor] = useState("");
  const [entryOpen, setEntryOpen] = useState(false);  // 체결 입력 폼 펼침 (2026-08-29 일지 개편)
  const [signal, setSignal] = useState<Signal | null>(null);
  const [curve, setCurve] = useState<{ date: string; equity: number; index: number }[]>([]);
  const eqRef = useRef<HTMLDivElement>(null);
  const eqApi = useRef<IChartApi | null>(null);

  const load = useCallback(async (id: number | null) => {
    let sid: number | null = id;
    const pl = await apiFetch("/portfolios");
    let mine: PortfolioItem[] = [];
    if (pl.ok) {
      mine = ((await pl.json()) as { items: PortfolioItem[] }).items
        .filter((it) => (it.market ?? "KR") === market);
      setPortfolios(mine);
    }
    if (id === null && market === "US") {
      // 미국은 기본 계좌 개념 없음 — 첫 미국 포트 자동 선택, 없으면 시작 패널만
      if (mine.length === 0) { setSum(null); setSignal(null); setJournal([]); setCurve([]); return; }
      sid = mine[0].id;
      setPid(sid);  // 표시 포트 = 등록 대상 포트 일치 보장 (2026-09-01 결함 수정: 등록이 KR 기본계좌로 새던 문제)
    }
    const res = await apiFetch(`/portfolio/summary${sid ? `?portfolio_id=${sid}` : ""}`);
    if (res.ok) {
      const sm = (await res.json()) as Summary;
      setSum(sm);
      sid = sm.portfolio.id;  // 기본 계좌 포함 — 주문표를 이 포트 기준으로
    }
    // 오늘의 주문표 — 선택된 실전 포트의 보유·현금 기준. 조회 시 '그날의 주문표'가 스냅샷 저장됨
    const sg = await apiFetch(`/signals/daily${sid ? `?portfolio_id=${sid}` : ""}`);
    if (sg.ok) setSignal((await sg.json()) as Signal);
    const jr = await apiFetch(`/portfolio/journal${sid ? `?portfolio_id=${sid}` : ""}`);
    if (jr.ok) setJournal(((await jr.json()) as { items: JournalItem[] }).items);
    const eq = await apiFetch(`/portfolio/equity${sid ? `?portfolio_id=${sid}` : ""}`);
    if (eq.ok) setCurve(((await eq.json()) as { items: { date: string; equity: number; index: number }[] }).items);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market]);

  useEffect(() => {
    void ensureSession().then((ok) => {
      if (!ok) { router.push("/login"); return; }
      void load(pid);
    });
  }, [pid, load, router]);

  // (마켓 전환 시 초기화는 MarketKeyed 의 key 리마운트가 담당 — mount 시 실행되는 effect 로 두면
  //  ?pid= 초기 선택을 덮어써서 제거, 2026-09-05)

  function prefillFill(o: { instrument: string; side: string; qty: number; price: number | null; kind: string }, date?: string) {
    const code200 = signal?.code_200
      ?? (market === "US" ? "QQQ" : sum?.positions.find((pp) => pp.code === "102110") ? "102110" : "069500");
    const codeLev = market === "US"
      ? (sum?.positions.find((pp) => pp.code === "TQQQ") ? "TQQQ" : "QLD") : "122630";
    setForm({ kind: o.side, code: o.instrument === "LEV" ? codeLev : code200,
      qty: String(o.qty), price: o.price ? String(market === "US" ? o.price / 100 : o.price) : "", amount: "",
      memo: ORDER_KIND_KO[o.kind] ?? o.kind,
      date: date ?? new Date().toISOString().slice(0, 10) });
    setEntryOpen(true);
    setTimeout(() => document.getElementById("fill-entry")?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
  }

  async function deleteTx(id: number) {
    if (!window.confirm("이 거래를 삭제할까요? 남은 거래로 보유·실현손익이 다시 계산됩니다.")) return;
    const res = await apiFetch(`/positions/${id}`, { method: "DELETE" });
    if (res.ok) { void load(pid); }
    else window.alert(((await res.json()) as { detail?: string }).detail ?? `삭제 실패 (${res.status})`);
  }

  async function submit() {
    setMsg("");
    const today = new Date().toISOString().slice(0, 10);
    const body: Record<string, unknown> = {
      portfolio_id: pid ?? undefined, kind: form.kind, memo: form.memo || undefined,
      // 선택한 날짜의 장 마감 시각으로 기록 — 오늘이면 현재 시각 (2026-09-01 편의성 개선)
      executed_at: form.date && form.date !== today ? `${form.date}T15:30:00+09:00` : new Date().toISOString(),
    };
    if (form.kind === "buy" || form.kind === "sell") {
      body.code = form.code; body.qty = Number(form.qty); body.price = priceToApi(market, form.price);
    } else {
      body.amount = priceToApi(market, form.amount);
    }
    const res = await apiFetch("/positions", { method: "POST", body: JSON.stringify(body) });
    if (res.ok) {
      const out = (await res.json()) as { realized_pnl: number | null };
      setMsg(out.realized_pnl !== null ? `등록됨 — 실현손익 ${fm(out.realized_pnl)}` : "등록됨");
      void load(pid);
    } else {
      setMsg(((await res.json()) as { detail?: string }).detail ?? `등록 실패 (${res.status})`);
    }
  }

  async function startPortfolio() {
    const name = newName.trim() || `실전매매 ${new Date().toISOString().slice(0, 10)}`;
    const res = await apiFetch("/portfolios", { method: "POST", body: JSON.stringify({
      name, market, code_200: market === "KR" ? startCode200 : undefined }) });
    if (!res.ok) return;
    const { id } = (await res.json()) as { id: number };
    // 시작 항목(입금·보유분)은 '최근 종가일' 15:30 KST 로 기록 — 신호 기준일 종가 시점 상태에
    // 포함되어야 다음 주문표부터 보유가 반영된다 (B안). 장 마감 후(당일 종가 적재 후) 시작하면
    // 당일로, 장중·개장 전이면 직전 종가일로 자연히 기록된다 (2026-09-02: 밤 시작이 전일로 찍히던 문제)
    let baseDay = "";
    try {
      const code0 = market === "US" ? "QQQ" : startCode200;
      const to = new Date().toISOString().slice(0, 10);
      const from = new Date(Date.now() - 15 * 86400e3).toISOString().slice(0, 10);
      const r = await fetch(`/api/ohlcv?code=${code0}&from=${from}&to=${to}`);
      if (r.ok) {
        const items = ((await r.json()) as { items: { date: string }[] }).items;
        if (items.length) baseDay = items[items.length - 1].date;
      }
    } catch { /* 폴백 사용 */ }
    if (!baseDay) {
      const prev = new Date();
      do { prev.setDate(prev.getDate() - 1); } while (prev.getDay() === 0 || prev.getDay() === 6);
      baseDay = `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}-${String(prev.getDate()).padStart(2, "0")}`;
    }
    const now = `${baseDay}T15:30:00+09:00`;
    const cash = startCash.trim() ? priceToApi(market, startCash) : 0;
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
        let price = h.price.trim() ? priceToApi(market, h.price) : 0;
        if (!price) {
          const to = new Date().toISOString().slice(0, 10);
          const from = new Date(Date.now() - 15 * 86400e3).toISOString().slice(0, 10);
          const r = await fetch(`/api/ohlcv?code=${h.code}&from=${from}&to=${to}`);
          if (r.ok) {
            const items = ((await r.json()) as { items: { close: number }[] }).items;
            if (items.length) price = items[items.length - 1].close;
          }
        }
        if (price > 0) {
          withPrice.push({ code: h.code, qty, price });
        } else {
          // 시세 미확보 행을 조용히 버리면 일부 종목만 등록되는 사고 — 중단하고 알림 (2026-09-01 결함 수정)
          window.alert(`${h.code} 의 최근 종가를 찾을 수 없습니다 — 평단을 직접 입력하거나 시세 적재 후 다시 시도하세요. (아무 것도 등록되지 않았습니다)`);
          await apiFetch(`/portfolios/${id}`, { method: "DELETE" });  // 빈 포트 롤백
          return;
        }
      }
      const rows = withPrice;
      const cost = rows.reduce((a, h) => a + h.qty * h.price, 0);
      if (cash + cost > 0) {
        await apiFetch("/positions", { method: "POST", body: JSON.stringify({
          portfolio_id: id, kind: "deposit", amount: cash + cost, executed_at: now, memo: "시작 입금 (현금+보유 원가)" }) });
      }
      for (const h of rows) {
        const res = await apiFetch("/positions", { method: "POST", body: JSON.stringify({
          portfolio_id: id, kind: "buy", code: h.code, qty: h.qty, price: h.price,
          executed_at: now, memo: "보유분 등록" }) });
        if (!res.ok) {
          // 등록 실패를 삼키면 일부 종목만 저장됨 (2026-09-01 원격 결함) — 알리고 중단
          const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail;
          window.alert(`${h.code} 등록 실패: ${detail ?? res.status} — 이 종목부터 등록되지 않았습니다. 시세 시딩 상태를 확인하세요.`);
          break;
        }
      }
    }
    setShowStart(false); setNewName(""); setStartCash("");
    setHoldings([{ code: market === "US" ? "QQQ" : startCode200, qty: "", price: "" }]);
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
  // 손익 비율 이중 기준 (feature-portfolio §5): 순손익 ÷ 납입원금, 평가손익 ÷ 보유원가 — 분모 ≤ 0 이면 % 미표시
  const netPct = sum && sum.principal > 0 ? net / sum.principal : null;
  const evalPct = sum && sum.invested_cost > 0 ? sum.unrealized_pnl / sum.invested_cost : null;
  // %는 작은 글씨·한 덩어리로 — 카드가 좁으면 금액 아래 줄로 자연 줄바꿈 (2026-09-02 지시)
  const withPct = (amount: string, pct: number | null) => pct == null ? amount
    : <>{amount} <span className="whitespace-nowrap text-[14px]">({fmtPct(pct, 2)})</span></>;

  return (
    <main>
      <PageTitle title={`실전매매 · ${MARKET_LABEL[market]}`} sub="체결 내역을 등록해 매수 시점 기준 수익률을 추적합니다 — 지연 시세 기준" />

      {/* 포트 선택: 드롭다운 → 탭(알약) — 한 번의 클릭으로 전환 (2026-09-02 지시) */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* '내 계좌 (기본)' 별칭 탭 제거 — 서버 기본값은 가장 오래된 포트와 동일해 중복 (2026-09-02 질문) */}
        {portfolios.map((p) => {
          const sel = (pid ?? sum?.portfolio.id) === p.id;
          return (
            <button key={p.id} onClick={() => setPid(p.id)}
              // 사용자 지정 배경색은 20% 틴트 — 선택 여부는 테두리·굵기로 (2026-09-05 지시)
              style={p.color ? { backgroundColor: `${p.color}33`, borderColor: sel ? undefined : `${p.color}88` } : undefined}
              className={`rounded-lg border px-3.5 py-2 text-[14px] transition-colors ${
                sel ? `border-accent font-semibold ${p.color ? "text-ink" : "bg-accent-dim text-accent"}`
                    : `border-line text-muted hover:border-line-strong hover:text-ink ${p.color ? "" : "bg-inset"}`}`}>
              {p.name}
            </button>
          );
        })}
        <button className="btn btn-primary !py-2" onClick={() => setShowStart(!showStart)}>＋ 새 실전매매</button>
        <span className="ml-auto flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-[13px] text-muted">
            <input type="checkbox" className="accent-[#b45309]" checked={includeCosts} onChange={(e) => setIncludeCosts(e.target.checked)} />
            비용 포함 (추정 수수료)
          </label>
          {sum?.as_of && <span className="text-xs text-faint">기준일 {sum.as_of} · 지연 시세</span>}
          {/* 이름·배경색 편집 (2026-09-05 지시) — 탭이 많아지면 이름과 색으로 구분 */}
          <button className="rounded-lg border border-line bg-inset px-3 py-1.5 text-[13px] text-muted transition-colors hover:border-accent hover:text-accent"
            onClick={() => {
              if (!sum) return;
              setEditName(sum.portfolio.name);
              setEditColor(portfolios.find((p) => p.id === sum.portfolio.id)?.color ?? "");
              setEditOpen(!editOpen);
            }}>✏️ 이름·색</button>
          {/* 파괴적 액션은 탭 줄과 분리하되 명확히 보이게 — 확인 대화상자로 이중 안전 (2026-09-02) */}
          <button className="rounded-lg border border-line bg-inset px-3 py-1.5 text-[13px] text-muted transition-colors hover:border-down hover:text-down"
            onClick={() => void deletePortfolio()}>🗑 이 포트 삭제</button>
        </span>
      </div>

      {editOpen && sum && (
        <Card className="mb-4 max-w-xl border-accent">
          <CardTitle>포트 이름 · 탭 배경색</CardTitle>
          <div className="grid gap-3">
            <label className="grid gap-1 text-[13px] text-faint">이름 (60자 이내)
              <input className="input" value={editName} maxLength={60} onChange={(e) => setEditName(e.target.value)} /></label>
            <div className="grid gap-1 text-[13px] text-faint">탭 배경색
              <div className="flex flex-wrap items-center gap-2">
                <button title="색 없음" onClick={() => setEditColor("")}
                  className={`h-8 w-8 rounded-lg border text-[11px] text-faint ${editColor === "" ? "border-accent ring-2 ring-accent/40" : "border-line"}`}>
                  없음
                </button>
                {TAB_COLORS.map((c) => (
                  <button key={c} title={c} onClick={() => setEditColor(c)}
                    style={{ backgroundColor: `${c}33`, borderColor: c }}
                    className={`h-8 w-8 rounded-lg border-2 ${editColor === c ? "ring-2 ring-accent/60" : ""}`} />
                ))}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button className="btn btn-primary" onClick={() => void (async () => {
                const r = await apiFetch(`/portfolios/${sum.portfolio.id}`, {
                  method: "PATCH", body: JSON.stringify({ name: editName.trim(), color: editColor }) });
                if (r.ok) { setEditOpen(false); void load(pid); }
                else window.alert(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `변경 실패 (${r.status})`);
              })()}>저장</button>
              <button className="btn" onClick={() => setEditOpen(false)}>취소</button>
              <span className="rounded-lg border px-3 py-1.5 text-[13px]"
                style={editColor ? { backgroundColor: `${editColor}33`, borderColor: `${editColor}88` } : undefined}>
                {editName.trim() || "미리보기"}
              </span>
            </div>
          </div>
        </Card>
      )}

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
              <label className="grid gap-1 text-[13px] text-faint">{startMode === "fresh" ? `초기 입금(${unit}, 선택)` : `보유 외 현금(${unit})`}
                <input className="input w-44" placeholder="예: 50000000" value={startCash}
                  onChange={(e) => setStartCash(e.target.value)} /></label>
            </div>
            {market === "KR" && (
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[13.5px]">
                <span className="font-semibold text-muted">주력 ETF 조합</span>
                {[["102110", "TIGER 200 (보수 0.05% — 권장)"], ["069500", "KODEX 200 (보수 0.15%)"]].map(([c, label]) => (
                  <button key={c} onClick={() => setStartCode200(c)}
                    className={`rounded-lg border px-3 py-1.5 transition-colors ${startCode200 === c ? "border-accent bg-accent-dim font-semibold" : "border-line bg-inset hover:border-line-strong"}`}>
                    {label}
                  </button>
                ))}
                <span className="text-[12px] text-faint">레버리지는 KODEX 공통 · 주문표가 이 종목 기준으로 계산됩니다</span>
              </div>
            )}
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
                      {MARKET_CODES[market].map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
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
                <button className="btn-ghost btn w-fit !py-1.5 text-[13.5px]" onClick={() => setHoldings([...holdings, { code: market === "US" ? "QQQ" : startCode200, qty: "", price: "" }])}>
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

      {/* 새 실전매매 작성 중에는 기존 포트 내용을 숨김 — 새 계좌 만드는 화면에 이전 내역이 섞여 보이는 혼동 방지 (2026-09-02 지시) */}
      {!showStart && <>
      {sum && (
        <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
          <Stat label="총자산" value={fm(sum.total_equity)} tip="현금 + 보유 주식 평가액(최근 종가 기준)의 합" />
          <Stat label="현금" value={fm(sum.cash)} tip="입금 − 출금 − 매수금액 + 매도금액의 원장 잔액" />
          <Stat label="주식" value={fm(sum.stock_value)} tip="보유 수량 × 최근 종가 (지연 시세)" />
          <Stat label="실현손익" value={fm(sum.realized_pnl)} tone={pnlTone(sum.realized_pnl)} tip="매도로 확정된 손익의 누적 — 매도가와 매수가(FIFO 선입선출 매칭)의 차이" />
          <Stat label="평가손익" value={withPct(fm(sum.unrealized_pnl), evalPct)} tone={pnlTone(sum.unrealized_pnl)} tip="아직 팔지 않은 보유분의 손익 — (현재가 − 평균단가) × 보유 수량. %는 보유원가 대비" />
          <Stat label={`순손익${includeCosts ? " (비용차감)" : ""}`} value={withPct(fm(net), netPct)} tone={pnlTone(net)} tip="실현손익 + 평가손익 − 추정 수수료(체크 시). %는 납입 원금(입금−출금) 대비 — 원금 이상 출금 시 %는 표시하지 않습니다" />
          <Stat label="TWR" value={fmtPct(sum.twr, 2)} tip="시간가중수익률 — 입출금 시점의 영향을 제거한 운용 성과. 펀드 수익률과 같은 방식이며, 입금이 많아도 왜곡되지 않습니다" />
          <Stat label="XIRR" value={fmtPct(sum.xirr, 2)} tip="내부수익률(연환산) — 입출금 현금흐름과 현재 평가액으로 계산한 '내 돈 기준' 연 수익률" />
        </div>
      )}

      {/* 보유 종목 — 상단 현황판 바로 아래 (2026-08-29 지시) */}
      {sum?.positions.length === 0 ? (
        <EmptyState icon="📒" title="보유 포지션이 없습니다"
          desc="HTS에서 체결한 매수 내역을 위 폼으로 등록하면 수익률 추적이 시작됩니다. 입출금도 등록해야 TWR·XIRR이 정확해집니다." />
      ) : (
        <div className="mb-4 grid gap-3">
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
                    {fmtPct(p.return, 2)} <span className="text-[13px] font-semibold">({fm(p.unrealized)})</span>
                  </div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-[14.5px] text-muted sm:grid-cols-4 lg:grid-cols-6">
                  <span>보유 <b className="text-ink">{p.qty.toLocaleString()}주</b></span>
                  <span>평단 <b className="text-ink">{fm(p.avg_price)}</b></span>
                  <span>현재가 <b className="text-ink">{fm(p.price)}</b></span>
                  <span>평가액 <b className="text-ink">{fm(p.value)}</b></span>
                  <span>연환산 <b className="text-ink">{p.annualized === null ? "— (30일 미만)" : fmtPct(p.annualized)}</b></span>
                  <span>최고/최저 <b className="text-up">{fmtPct(p.best_return)}</b> / <b className="text-down">{fmtPct(p.worst_return)}</b></span>
                </div>
                {hasBand && (
                  <div className="mt-3">
                    <div className="mb-1 flex justify-between text-[11px] text-faint">
                      <span>손절 {fm(p.stop_price!)}</span>
                      <span>목표 {fm(p.target_price!)}</span>
                    </div>
                    <GaugeBar ratio={ratio} color={t === "down" ? "var(--color-down)" : "var(--color-up)"} height={6} />
                  </div>
                )}
              </Card>
            );
          })}
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

      {/* 오늘의 주문표 (2026-08-28 지시 — 실전매매 중간 섹션) */}
      <Card className="mb-4">
        <CardTitle right={<Link href={`/signals${market === "US" ? "?market=US" : ""}`} className="text-[13.5px] font-semibold normal-case text-accent">전체 주문표 →</Link>}>
          {(() => {
            const today = new Date().toISOString().slice(0, 10);
            const ed = signal?.exec_day;
            if (!ed) return "오늘의 주문표";
            return ed === today
              ? `오늘(${ed.slice(5)}) 실행 주문표 — 확정`
              : `${ed.slice(5)} 실행 예정 주문표`;
          })()} {signal?.status === "OK" && (
            <span className="normal-case text-faint">· {signal.trade_date} 종가 · {REGIME_KO2[signal.regime ?? ""]} · E {fmtPct(signal.e_target)}
              {signal.basis === "portfolio" && signal.account
                ? ` · 계산 기준(${signal.trade_date} 종가 시점): 보유 ${signal.account.qty_200.toLocaleString()}주/레버 ${signal.account.qty_lev.toLocaleString()}주 · 현금 ${fm(signal.account.cash)} — 오늘 체결 등록은 내일 주문표부터 반영`
                : " · 모델 기준"}
              {/* 공식 출처 — 포트 동결(전환 시 변수)이면 도움말 풍선으로 변수 상세 표기 (2026-09-05 지시) */}
              {signal.algo_source === "portfolio" && (
                <span>{" · "}
                  <Tip tip={(signal.algo_detail?.length ?? 0) > 0 ? (
                    <span>
                      <b className="text-ink">이 포트에 동결된 매매 공식 변수</b> — 시뮬레이션 전환 시점 값으로 고정되며,
                      알고리즘 설정을 바꿔도 이 포트에는 적용되지 않습니다.<br />
                      {signal.algo_detail!.map((d) => (
                        <span key={d.key}>· {d.label}: <b className="text-ink">{d.value}</b>
                          {d.default !== null && <span className="text-faint"> (기본 {d.default})</span>}<br /></span>
                      ))}
                    </span>
                  ) : (
                    <span>전환 시점의 <b className="text-ink">기본값으로 동결</b>된 공식입니다 — 이후 알고리즘 설정
                      변경이 이 포트에는 적용되지 않습니다.</span>
                  )}>
                    <b className="cursor-help text-accent">공식: 이 포트 고정
                      {(signal.algo_detail?.length ?? 0) > 0 && ` (변수 ${signal.algo_detail!.length}건)`}</b>
                    <span className="text-faint">ⓘ</span>
                  </Tip>
                </span>
              )}
              {signal.algo_source === "settings" && " · 공식: 알고리즘 설정 기준"}</span>
          )}
        </CardTitle>
        {signal?.status === "OK" && signal.orders && signal.orders.length > 0 ? (
          <div className="overflow-x-auto">
            {/* 모바일: 줄바꿈 금지 + 축약(종목 짧게·작은 글씨)으로 한 화면에 — 넘치면 가로 스크롤 (2026-09-02 지시) */}
            <table className="w-full whitespace-nowrap text-[13px] sm:text-[14.5px]">
              <thead><tr className="border-b border-line text-left text-[13px] text-faint">
                <th className="pb-2 font-medium">구분</th><th className="pb-2 font-medium">종목</th>
                <th className="pb-2 font-medium">방향</th>
                <th className="pb-2 text-right font-medium">방식 · 가격</th>
                <th className="pb-2 text-right font-medium">수량<span className="hidden sm:inline">{signal?.basis === "portfolio" ? " (내 계좌 기준)" : " (모델 1억)"}</span></th>
                <th className="pb-2 pl-4 font-medium">체결</th>
              </tr></thead>
              <tbody>
                {signal.orders.map((o, i) => (
                  <tr key={i} className="border-b border-line/50 last:border-0">
                    <td className="py-2"><Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{ORDER_KIND_KO[o.kind] ?? o.kind}</Badge></td>
                    <td className="py-2">
                      {(() => {
                        const full = o.instrument === "K200" ? (signal?.name_200 ?? (market === "US" ? "QQQ" : "KODEX 200")) : (market === "US" ? "레버리지(QLD/TQQQ)" : "KODEX 레버리지");
                        const short = o.instrument === "K200" ? full.split(" ")[0] : "레버";
                        return <><span className="hidden sm:inline">{full}</span><span className="sm:hidden">{short}</span></>;
                      })()}
                    </td>
                    <td className={`py-2 font-bold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                    <td className="table-num py-2 font-semibold">
                      {o.price
                        // 모바일은 지정가 배지 생략(지정가가 기본) — 시장가만 배지 유지 (2026-09-02 폭 축약)
                        ? <><span className="mr-1 hidden rounded bg-raised px-1.5 py-0.5 text-[11px] font-bold text-muted sm:inline">지정가</span>{fpx(o.price)}</>
                        : <><span className="mr-1 rounded bg-accent/15 px-1.5 py-0.5 text-[11px] font-bold text-accent">시장가</span><span className="text-[12px] text-faint">시가</span></>}
                    </td>
                    <td className="table-num py-2">{o.qty.toLocaleString()}주</td>
                    <td className="py-2 pl-2 sm:pl-4">
                      <button className="btn !px-2.5 !py-1 text-[12.5px]" onClick={() => prefillFill(o)}>
                        <span className="sm:hidden">등록</span><span className="hidden sm:inline">체결 등록</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {signal.gap_cancel_below && (
              <p className="mt-2 text-[13px] text-faint">⚠️ 시가 {fpx(signal.gap_cancel_below)} 이하 출발 시 그리드 전량 취소 — 상세는 전체 주문표 참조</p>
            )}
          </div>
        ) : (
          <p className="text-[14px] text-faint">
            {signal?.status === "OK" ? "오늘은 신규 주문이 없습니다." : "시그널이 아직 없습니다 — 장 마감 배치(16:05) 이후 표시됩니다."}
          </p>
        )}
        {/* 체결 입력 — 장 마감 후 실제 체결만 등록. 주문 행의 '체결 등록'이 값을 채워줌 (2026-08-29 일지 개편) */}
        <details id="fill-entry" className="mt-3 rounded-xl border border-line bg-inset px-4 py-3" open={entryOpen}
          onToggle={(e) => setEntryOpen((e.target as HTMLDetailsElement).open)}>
          <summary className="cursor-pointer text-[13.5px] font-semibold text-accent">
            체결·입출금 등록 <span className="font-normal text-faint">— 장 마감 후 실제 체결된 것만 입력하면 다음 주문표에 반영됩니다</span>
          </summary>
          <div className="mt-3">
            <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1 text-xs text-faint">체결일
            <input type="date" className="input" value={form.date}
              onChange={(e) => setForm({ ...form, date: e.target.value })} /></label>
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
                  {MARKET_CODES[market].map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
                </select>
              </label>
              <label className="grid gap-1 text-xs text-faint">수량
                <input className="input w-24" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} /></label>
              <label className="grid gap-1 text-xs text-faint">wontouch
                <input className="input w-32" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
            </>
          ) : (
            <label className="grid gap-1 text-xs text-faint">금액({unit})
              <input className="input w-40" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
          )}
          <label className="grid gap-1 text-xs text-faint">메모
            <input className="input w-44" value={form.memo} onChange={(e) => setForm({ ...form, memo: e.target.value })} /></label>
          <button className="btn btn-primary" onClick={() => void submit()}>등록</button>
          {msg && <span className="text-[13px] text-muted">{msg}</span>}
        </div>
          </div>
        </details>
      </Card>

      {/* 일자별 매매 일지 — 그날의 주문표 + 체결 + 수익률 (시뮬레이터 저널과 동일 구성, 2026-08-29 지시) */}
      {journal.length > 0 && (
        <Card className="mt-4">
          <CardTitle>일자별 매매 일지 <span className="normal-case text-faint">· 계획 → 체결 → 수익률 · 총 {journal.length}일 중 최근 {Math.min(txDays, journal.length)}일</span></CardTitle>
          <div className="grid gap-1.5">
            {journal.slice(0, txDays).map((j) => (
              <details key={j.date} className="rounded-xl border border-line bg-inset">
                <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-4 py-3 text-[14.5px] transition-colors hover:bg-raised/60">
                  <b className="w-24">{j.date}</b>
                  {j.date > new Date().toISOString().slice(0, 10) && <Badge tone="default">다음 거래일 예정</Badge>}
                  {j.regime && (
                    <Badge tone={j.regime === "BULL" ? "up" : j.regime === "BEAR" ? "down" : "accent"}>{REGIME_KO2[j.regime]}</Badge>
                  )}
                  {j.day_return !== null && (
                    <span className={`w-20 text-right font-bold ${j.day_return > 0 ? "text-up" : j.day_return < 0 ? "text-down" : "text-muted"}`}>
                      {(j.day_return * 100).toFixed(2)}%
                    </span>
                  )}
                  {j.equity !== null && <span className="hidden text-[13px] text-muted md:inline">평가 {fm(j.equity)}</span>}
                  {j.realized_pnl !== 0 && (
                    <span className={`text-[13.5px] font-bold ${j.realized_pnl > 0 ? "text-up" : "text-down"}`}>
                      실현 {j.realized_pnl >= 0 ? "+" : ""}{fm(j.realized_pnl)}
                    </span>
                  )}
                  <span className="ml-auto text-[13px] text-faint">주문 {j.planned ? j.planned.length : "—"} · 체결 {j.date > new Date().toISOString().slice(0, 10) ? "—" : j.fills.length}</span>
                </summary>
                <div className="grid gap-x-8 gap-y-4 border-t-2 border-line-strong px-4 py-4 lg:grid-cols-2">
                  <div>
                    <div className="mb-2 text-[13.5px] font-semibold text-muted">📋 장 시작 전 주문표 (계획) ({j.planned?.length ?? 0}건)</div>
                    {j.planned === null ? (
                      <p className="text-[13px] text-faint">이날의 주문표 스냅샷이 없습니다 — 주문표를 화면에서 조회한 날부터 자동 저장됩니다.</p>
                    ) : j.planned.length === 0 ? (
                      <p className="text-[13px] text-faint">신규 주문 없음.</p>
                    ) : (
                      <div className="overflow-x-auto">
                      {/* 모바일: 금액 열 숨김(가격×수량으로 유도 가능) — 한 화면 폭에 맞춤 (2026-09-02 지시) */}
                      <table className="w-full whitespace-nowrap text-[13px] sm:text-[14px]">
                        <thead><tr className="text-left text-xs text-faint">
                          <th className="pb-1 font-medium">구분</th><th className="pb-1 font-medium">종목</th>
                          <th className="pb-1 font-medium">방향</th>
                          <th className="pb-1 text-right font-medium">방식 · 가격</th>
                          <th className="pb-1 text-right font-medium">수량</th>
                          <th className="hidden pb-1 text-right font-medium sm:table-cell">금액</th>
                          <th className="pb-1" />
                        </tr></thead>
                        <tbody>
                          {j.planned.map((o, i) => (
                            <tr key={i} className="border-t border-line/40">
                              <td className="py-1.5 text-muted">{ORDER_KIND_KO[o.kind] ?? o.kind}</td>
                              <td className="py-1.5">{o.instrument === "K200" ? (market === "US" ? "QQQ" : "200 ETF") : "레버리지"}</td>
                              <td className={`py-1.5 font-semibold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                              <td className="table-num py-1.5">
                              {o.price
                                ? <><span className="mr-1 rounded bg-raised px-1 py-0.5 text-[10.5px] font-bold text-muted">지정가</span>{fpx(o.price)}</>
                                : <><span className="mr-1 rounded bg-accent/15 px-1 py-0.5 text-[10.5px] font-bold text-accent">시장가</span></>}
                            </td>
                              <td className="table-num py-1.5">{o.qty.toLocaleString()}</td>
                              <td className="table-num hidden py-1.5 text-muted sm:table-cell">{o.price ? fm(o.price * o.qty) : "—"}</td>
                              <td className="py-1.5 pl-2 text-right">
                                <button className="btn !px-2 !py-0.5 text-[11.5px]"
                                  title="이 주문의 체결을 이 날짜로 등록 (수량·가격 수정 가능)"
                                  onClick={() => prefillFill(o, j.date)}>체결 등록</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      </div>
                    )}
                    {j.gap_cancel_below && (
                      <p className="mt-2 text-[12.5px] text-faint">⚠️ 시가 {j.gap_cancel_below.toLocaleString()}원 이하 출발 시 그리드 취소</p>
                    )}
                  </div>
                  <div className="border-t border-dashed border-line-strong pt-4 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0" style={{ borderLeftStyle: "solid" }}>
                    <div className="mb-2 flex items-center justify-between text-[13.5px] font-semibold text-muted">
                      <span>✅ 체결 내역 ({j.fills.length}건)</span>
                      {j.realized_pnl !== 0 && (
                        <Tip tip={<span>이날 등록된 매도들의 실현손익 합 — (매도가 − FIFO 매수가) × 수량.<br />아직 팔지 않은 보유분의 평가손익은 포함되지 않습니다 (그건 상단 일간 수익률에 반영).</span>}>
                          <span className={`font-bold ${j.realized_pnl > 0 ? "text-up" : "text-down"}`}>
                            당일 실현손익 {j.realized_pnl >= 0 ? "+" : ""}{fm(j.realized_pnl)} <span className="font-normal text-faint">ⓘ</span>
                          </span>
                        </Tip>
                      )}
                    </div>
                    {j.fills.length === 0 ? (
                      <p className="text-[13px] text-faint">
                        {j.date === new Date().toISOString().slice(0, 10)
                          ? "아직 등록된 체결이 없습니다 — 장 마감 후 위 주문표의 '체결 등록'으로 입력하세요."
                          : "없음"}
                      </p>
                    ) : (
                      <div className="overflow-x-auto">
                      <table className="w-full whitespace-nowrap text-[13px] sm:text-[14px]">
                        <thead><tr className="text-left text-xs text-faint">
                          <th className="pb-1 font-medium">구분</th><th className="pb-1 font-medium">종목</th>
                          <th className="pb-1 font-medium">방향</th>
                          <th className="pb-1 text-right font-medium">체결가/금액</th>
                          <th className="pb-1 text-right font-medium">수량</th>
                          <th className="hidden pb-1 text-right font-medium sm:table-cell">금액</th>
                          <th className="pb-1" />
                        </tr></thead>
                        <tbody>
                          {j.fills.map((t) => (
                            <tr key={t.id} className="border-t border-line/40">
                              {/* 메모 표기 한도 10→18자 — '시작 입금 (현금+보유 원가)' 가 '입금'으로만 보여 금액 오해 (2026-09-02) */}
                              <td className="py-1.5 text-muted">{t.memo && t.memo.length <= 18 ? t.memo : TX_KO[t.kind]}</td>
                              <td className="py-1.5">{t.name ?? "—"}</td>
                              <td className={`py-1.5 font-semibold ${t.kind === "buy" ? "text-up" : t.kind === "sell" ? "text-down" : "text-muted"}`}>{TX_KO[t.kind]}</td>
                              <td className="table-num py-1.5">{fpx(t.price ?? t.amount ?? 0)}</td>
                              <td className="table-num py-1.5">{t.qty ? t.qty.toLocaleString() : "—"}</td>
                              <td className="table-num hidden py-1.5 text-muted sm:table-cell">{fm(t.price && t.qty ? t.price * t.qty : (t.amount ?? 0))}</td>
                              <td className="py-1.5 pl-2 text-right">
                                <button className="text-[12px] text-faint transition-colors hover:text-down" title="오입력 삭제 — 남은 거래로 재계산"
                                  onClick={() => void deleteTx(t.id)}>✕</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      </div>
                    )}
                  </div>
                  {j.account && (
                    <div className="col-span-full flex flex-wrap gap-x-6 gap-y-1 border-t border-line pt-3 text-[13.5px] text-muted">
                      {j.e_target !== null && <span>노출 E <b className="text-ink">{(j.e_target * 100).toFixed(1)}%</b></span>}
                      <span>현금 <b className="text-ink">{fm(j.account.cash)}</b></span>
                      <span>보유 200 ETF <b className="text-ink">{j.account.qty_200.toLocaleString()}주</b></span>
                      <span>레버리지 <b className="text-ink">{j.account.qty_lev.toLocaleString()}주</b></span>
                    </div>
                  )}
                </div>
              </details>
            ))}
          </div>
          {journal.length > txDays && (
            <button className="btn mt-3 w-full" onClick={() => setTxDays((v) => v + 30)}>
              이전 일지 더 보기 ({journal.length - txDays}일 남음)
            </button>
          )}
        </Card>
      )}
      </>}
    </main>
  );
}
