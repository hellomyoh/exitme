"use client";

/** 실전매매 — 현황·보유·수익률 + 오늘의 주문표(체결 등록) + 일자별 매매 일지 (feature-portfolio §9, 2026-08-29 개편). */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { apiFetch, ensureSession } from "../../lib/api";
import { fmtMoneyM, fmtPriceM, MARKET_CODES, MARKET_LABEL, marketOf, priceToApi } from "../../lib/market";
import { Badge, Card, CardTitle, EmptyState, fmtPct, GaugeBar, PageTitle, pnlTone, Stat, Tip } from "../../components/ui";
import MarketSwitch from "../../components/marketswitch";

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
type PortfolioItem = { id: number; name: string; kind: string; market?: string; color?: string | null; etf?: string | null };
// 미국 포트 공식 (2026-09-06 지시) — 주문표 분기 키. 구형 포트(etf 없음)는 TF
const US_FORMULAS: { key: string; label: string; short: string; guide: string; desc: string }[] = [
  { key: "LTM_QLD", label: "LTM · QQQ + QLD", short: "LTM · QLD", guide: "/guide/ltm", desc: "미국 기본 — 추세 위·1년 수익 양·급락 없음이면 2배(QLD), 아니면 1배" },
  { key: "LTM_TQQQ", label: "LTM · QQQ + TQQQ", short: "LTM · TQQQ", guide: "/guide/ltm", desc: "같은 규칙 · 2배 = QQQ 50% + TQQQ 50%" },
  { key: "QQQ_TF", label: "TF · QQQ 1배", short: "TF", guide: "/guide/tf", desc: "200일선 위 전량 보유 / 2% 이탈 시 전량 현금 — 레버리지 없음" },
];
const formulaOf = (etf?: string | null) => US_FORMULAS.find((f) => f.key === (etf ?? "QQQ_TF")) ?? US_FORMULAS[2];

// 탭 배경색 프리셋 — 라이트·다크 모두에서 20% 틴트로 사용 (2026-09-05 지시)
const TAB_COLORS = ["#f97316", "#2563eb", "#059669", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#64748b"];
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
type Signal = { status: string; exec_day?: string; trade_date?: string; regime?: string; e_target?: number; orders?: OrderRow[]; snapshot_missing?: boolean; name_lev?: string; strategy?: string; gap_cancel_below?: number; basis?: string; name_200?: string; code_200?: string; account?: { qty_200: number; qty_lev: number; cash: number }; algo_source?: "portfolio" | "settings"; algo_overrides?: Record<string, number>; algo_detail?: { key: string; label: string; value: number; default: number | null }[]; indicators?: Record<string, number>; reconcile?: { date: string; items: { level: string; text: string }[] } | null };

const TX_KO: Record<string, string> = { buy: "매수", sell: "매도", deposit: "입금", withdraw: "출금" };
const REGIME_KO2: Record<string, string> = { BULL: "상승장", NEUTRAL: "중립장", BEAR: "하락장" };
const ORDER_KIND_KO: Record<string, string> = {
  grid1: "그리드 1차", grid2: "그리드 2차", grid3: "그리드 3차", tp: "익절", reduce: "축소",
  lev_strat: "레버 전략", lev_tact1: "레버 전술1", lev_tact2: "레버 전술2", lev_tact_exit: "전술 이탈", lev_liq: "레버 청산",
  tf_entry: "추세 진입", tf_exit: "추세 이탈",
  ltm_entry: "LTM 진입", ltm_exit: "LTM 이탈(현금)", ltm_lever_on: "레버리지 ON", ltm_lever_off: "레버리지 OFF", ltm_rebal: "LTM 리밸런스",
};

const toneCls = { up: "text-up", down: "text-down", default: "text-ink" };

// 계산 근거 — 구 주문표 페이지에서 이관 (2026-09-05 메뉴 제거). 지표 라벨·주문별 실행 조건 설명.
const IND_LABELS: [string, string, "price" | "pct"][] = [
  ["close", "종가", "price"], ["ma20", "MA20 (20일 평균)", "price"], ["ma60", "MA60 (60일 평균)", "price"],
  ["ma200", "MA200 (200일 평균)", "price"], ["ema20", "EMA20", "price"], ["atr20", "ATR20 (변동폭)", "price"],
  ["grid", "그리드 간격", "pct"], ["sigma20", "σ20 (연율 총변동성)", "pct"],
  ["sigma_down", "σ_down (하방 변동성)", "pct"], ["sigma_ref", "σ_ref (250일 중위)", "pct"],
  ["gap_to_ma200", "MA200 대비 이격", "pct"], ["exit_level", "청산 기준선 (MA200 −2%)", "price"],
];
function orderCondDesc(o: OrderRow, ind: Record<string, number> | undefined): string {
  const grid = ind?.grid ?? 0;
  const close = ind?.close ?? 0;
  if (o.kind.startsWith("grid")) {
    const k = Number(o.kind.slice(4));
    return `전일 종가 ${close.toLocaleString()}에서 −${(grid * k * 100).toFixed(1)}% 하락 시 매수`;
  }
  const map: Record<string, string> = {
    tp: "보유 로트가 매수가 +Grid% 도달 시 익절",
    reduce: "목표 비중 초과분을 시가에 축소 매도",
    lev_strat: "레버리지 전략 트랙 — 목표 비중까지 시가 매수/매도",
    lev_tact1: "레버리지 눌림목 1차 (EMA20 −0.75×ATR 이탈)",
    lev_tact2: "레버리지 눌림목 2차 (EMA20 −1.5×ATR 이탈)",
    lev_tact_exit: "레버리지 전술 물량 이탈 (EMA20 회복)",
    lev_liq: "레버리지 전량 청산 (레짐 이탈/변동성 초과)",
    tf_entry: "종가가 MA200 위 — 다음날 시가 전량 매수",
    tf_exit: "종가가 MA200 −2% 관통 — 다음날 시가 전량 매도",
    ltm_entry: "종가가 MA200 위로 올라옴 — 다음날 시가에 목표 노출로 진입",
    ltm_exit: "종가가 MA200 −2% 관통 — 다음날 시가 전량 현금",
    ltm_lever_on: "12개월 모멘텀 양 · 최근 20일 −3% 급락 없음 — 레버리지로 노출 2.0",
    ltm_lever_off: "모멘텀 음전환 또는 급락 브레이커(20일) — 레버리지를 풀어 1배로",
    ltm_rebal: "목표 노출과 10% 이상 어긋남 — 시장가 리밸런스",
  };
  return map[o.kind] ?? "";
}


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
  const [showStart, setShowStart] = useState(sp?.get("start") === "1");  // 상단 바 '새 실전매매' 진입 (2026-09-05)
  const [startMode, setStartMode] = useState<"fresh" | "holdings">("fresh");
  const [startCode200, setStartCode200] = useState("102110");  // KR 주력 조합 — 기본 TIGER (보수 연 0.05%, 2026-09-01 지시)
  const [startCash, setStartCash] = useState("");
  const [startEtf, setStartEtf] = useState("LTM_QLD");  // US 공식 — 기본 LTM·QLD (2026-09-06 지시)
  const [holdings, setHoldings] = useState<{ code: string; qty: string; price: string }[]>([
    { code: market === "US" ? "QQQ" : "102110", qty: "", price: "" },
  ]);
  // 시작 패널 증권사 계좌 (2026-09-06 지시) — 고르면 시작과 함께 연결, KR 은 잔고를 불러와 입금·보유분을 미리 채움
  const [startAcct, setStartAcct] = useState("");
  const [acctBal, setAcctBal] = useState<AcctBalance | null>(null);
  const [acctMsg, setAcctMsg] = useState("");
  const [ccBusy, setCcBusy] = useState(false);
  const [journal, setJournal] = useState<JournalItem[]>([]);
  // 포트 이름·탭 배경색 편집 패널 (2026-09-05 지시)
  const [editOpen, setEditOpen] = useState(false);
  // 증권사 조회 연동 (2026-09-05 지시) — 체결 자동 가져오기 · 주문표 대조
  // 예수금 대조 (2026-09-06) — 15:45 동기화·'지금 대조'가 저장. diff = 계좌 D+2 예수금 − 원장 현금
  type CashCheck = { date: string; at: string; ledger_cash: number; account_cash: number; account_deposit: number; diff: number; tolerance: number; warn: boolean; aligned_at?: string; aligned_amount?: number };
  type BrokerInfo = { linked: boolean; id?: number; label?: string; env?: string; app_key?: string; account_no?: string; acnt_prdt_cd?: string; last_import_at?: string | null; cash_check?: CashCheck | null };
  // 시작 패널 '계좌에서 불러오기' 응답 (2026-09-06)
  type AcctBalance = { date: string; env: string; deposit: number; deposit_d2: number; deposit_d1: number; total_eval: number; strategy_count: number;
    holdings: { code: string; name: string; qty: number; avg_price: number; price: number; eval_amount: number; strategy: boolean }[] };
  type ImportRow = { date: string; code: string; name: string; side: string; qty: number; price: number; amount: number; status: string };
  const [broker, setBroker] = useState<BrokerInfo | null>(null);
  const [acctList, setAcctList] = useState<{ id: number; label: string; account_no: string; acnt_prdt_cd: string; env: string }[]>([]);
  const [imp, setImp] = useState<{ items: ImportRow[]; added: number; skipped: number; unknown_codes: string[] } | null>(null);
  const [impMsg, setImpMsg] = useState("");
  // 예약주문 (2026-09-05 지시) — 장 마감 후 주문표에서 버튼으로 접수, 줄별 등록/체결 상태 표시
  type BrokerOrderRow = { id: number | null; plan_date: string; line_key: string; code: string; instrument: string; kind: string; side: string;
    otype: string; qty: number; price: number | null; rsvn_ord_seq: string | null; order_no: string | null; filled_qty: number;
    status: string; status_ko: string; message: string | null; mode?: string };
  // 무인 실행 상태 (2026-09-06, ADR-008) — 설정 허용 스위치 + 포트 정지 상태 + 마지막 실행 요약
  // 완전 무인(자동 승인, 2026-09-07 지시) — 16:45 주문표 자동 승인 설정·마지막 실행
  type AutoApprove = { enabled: boolean; market_reserve: boolean; daily_buy_cap: number | null; updated_at?: string | null };
  type AutoApproveLast = { date: string; at: string; exec_day: string; approved: number; reserved: number; skipped: number; failed: number; manual: string[]; note?: string | null };
  type AutoExec = { allowed: { buy: boolean; sell: boolean; preopen_cancel?: boolean }; paused: boolean; paused_reason: string | null; paused_at: string | null;
    fail_streak: number; last_run: { date: string; at: string; open: number | null; gap_hit: boolean; submitted: number; skipped_gap: number; skipped: number; failed: number; note?: string } | null;
    auto_approve?: AutoApprove; auto_approve_last?: AutoApproveLast | null };
  // 장 시작 전 예상 시가 갭 취소 마지막 실행 (2026-09-06, app.preopen) — 08:57 예상체결가 판정 결과
  type PreopenRun = { date: string; at: string; expected: number | null; gap_exact: number | null; gap_hit: boolean;
    cancelled: number; failed: number; untracked: number; unmatched: number; note?: string | null };
  type BrokerOrders = { window: { open: boolean; reason: string }; items: BrokerOrderRow[]; auto_exec?: AutoExec; preopen?: { last_run?: PreopenRun | null } | null };
  const [bo, setBo] = useState<BrokerOrders | null>(null);
  const [boConfirm, setBoConfirm] = useState(false);
  const [boBusy, setBoBusy] = useState(false);
  const [boMsg, setBoMsg] = useState("");
  const [editName, setEditName] = useState("");
  const [editEtf, setEditEtf] = useState("LTM_QLD");  // US 공식 변경 (2026-09-06 지시)
  const [editColor, setEditColor] = useState("");
  const [entryOpen, setEntryOpen] = useState(false);  // 체결 입력 폼 펼침 (2026-08-29 일지 개편)
  const [signal, setSignal] = useState<Signal | null>(null);
  const [curve, setCurve] = useState<{ date: string; equity: number; index: number; pnl?: number }[]>([]);
  const eqRef = useRef<HTMLDivElement>(null);
  const eqApi = useRef<IChartApi | null>(null);

  // ── 예약주문 헬퍼 (2026-09-05) ──
  const lineKey = (o: OrderRow) => `${o.kind}:${o.instrument}:${o.side}:${o.otype}:${o.price ? Math.round(o.price) : "mkt"}`;
  const ACTIVE = ["reserved", "filled", "partial", "approved", "submitted"];  // 무인 승인·발주 줄도 '접수됨'으로 취급
  const boFor = (o: OrderRow): BrokerOrderRow | null => {
    if (!bo) return null;
    const same = bo.items.filter((i) => i.line_key === lineKey(o));
    return same.slice().reverse().find((i) => ACTIVE.includes(i.status)) ?? same[same.length - 1] ?? null;
  };
  const pendingLines = (signal?.orders ?? []).filter((o) => { const b = boFor(o); return !(b && ACTIVE.includes(b.status)); });
  const instName = (o: OrderRow) => o.instrument === "K200" ? (signal?.name_200 ?? "KODEX 200") : "KODEX 레버리지";
  // 줄 선택 (2026-09-05 지시: 체크해서 고른 줄만 등록) — 주문표가 바뀌면 접수 대기 줄 전체를 기본 선택
  const [sel, setSel] = useState<Set<string>>(new Set());
  const pendingKeys = pendingLines.map(lineKey).join("|");
  useEffect(() => { setSel(new Set(pendingKeys ? pendingKeys.split("|") : [])); }, [pendingKeys, signal?.exec_day]);
  const selectedLines = pendingLines.filter((o) => sel.has(lineKey(o)));
  const toggleSel = (o: OrderRow) => setSel((prev) => { const n = new Set(prev); const k = lineKey(o); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  async function reserveSelected() {
    if (!sum || !signal?.exec_day || selectedLines.length === 0) return;
    setBoBusy(true); setBoMsg("");
    const lines = selectedLines.map((o) => ({ instrument: o.instrument, kind: o.kind, side: o.side, otype: o.otype, qty: o.qty, price: o.price ?? null }));
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/orders/reserve`, { method: "POST", body: JSON.stringify({ date: signal.exec_day, lines }) });
    const j = (await r.json().catch(() => ({}))) as { reserved?: number; failed?: number; detail?: string };
    setBoBusy(false); setBoConfirm(false);
    if (!r.ok) { setBoMsg(j.detail ?? `접수 실패 (${r.status})`); return; }
    setBoMsg(`${j.reserved ?? 0}건 접수${(j.failed ?? 0) > 0 ? ` · ${j.failed}건 실패 — 아래 표의 ✗ 를 확인하세요` : ""}`);
    void load(pid);
  }
  // 무인 실행 승인 (2026-09-06) — 선택한 지정가 줄을 09:01 시가 확인 후 자동 발주 대상으로 등록
  const ae = bo?.auto_exec ?? null;
  const aeAllowedFor = (o: OrderRow) => !!ae && o.otype === "limit" && (o.side === "buy" ? ae.allowed.buy : ae.allowed.sell);
  const aeEligible = selectedLines.filter(aeAllowedFor);
  const aeBlocked = selectedLines.filter((o) => !aeAllowedFor(o));
  const [aeConfirm, setAeConfirm] = useState(false);
  async function approveSelected() {
    if (!sum || !signal?.exec_day || aeEligible.length === 0) return;
    setBoBusy(true); setBoMsg("");
    const lines = aeEligible.map((o) => ({ instrument: o.instrument, kind: o.kind, side: o.side, otype: o.otype, qty: o.qty, price: o.price ?? null }));
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/orders/approve`, { method: "POST", body: JSON.stringify({ date: signal.exec_day, lines }) });
    const j = (await r.json().catch(() => ({}))) as { approved?: number; failed?: number; detail?: string };
    setBoBusy(false); setAeConfirm(false);
    if (!r.ok) { setBoMsg(j.detail ?? `승인 실패 (${r.status})`); return; }
    setBoMsg(`${j.approved ?? 0}건 무인 실행 승인${(j.failed ?? 0) > 0 ? ` · ${j.failed}건 실패 — 아래 표를 확인하세요` : ""} — 실행일 09:01 시가 확인 후 발주됩니다`);
    void load(pid);
  }
  async function resumeAutoExec() {
    if (!sum) return;
    if (!window.confirm("무인 실행 정지를 해제할까요? 정지 사유를 확인하고 계좌·기록이 맞는지 점검한 뒤 켜세요.")) return;
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/auto-exec/resume`, { method: "POST" });
    if (r.ok) { setBoMsg("무인 실행을 다시 켰습니다"); void load(pid); }
  }
  // 완전 무인 운영 (2026-09-07 지시) — 자동 승인 설정 저장 · 살아 있는 주문 전량 취소(긴급 정지)
  const aa: AutoApprove = ae?.auto_approve ?? { enabled: false, market_reserve: true, daily_buy_cap: null };
  const [aaOpen, setAaOpen] = useState(false);
  const [aaForm, setAaForm] = useState<{ enabled: boolean; market_reserve: boolean; cap: string }>({ enabled: false, market_reserve: true, cap: "" });
  useEffect(() => { setAaForm({ enabled: aa.enabled, market_reserve: aa.market_reserve, cap: aa.daily_buy_cap ? String(aa.daily_buy_cap) : "" }); },
    [aa.enabled, aa.market_reserve, aa.daily_buy_cap]);
  const kstToday = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
  const liveOrders = (bo?.items ?? []).filter((i) => ["approved", "reserved", "submitted", "partial"].includes(i.status) && i.plan_date >= kstToday);
  async function saveAutoApprove() {
    if (!sum) return;
    if (aaForm.enabled && !aa.enabled && !window.confirm(
      "완전 무인 운영을 켭니다.\n\n· 매일 16:45 에 다음 실행일 주문표를 계산해 설정에서 허용한 방향의 지정가 줄을 자동 승인합니다 — 사람이 승인하지 않아도 09:01 에 발주됩니다.\n· 시장가 줄(레버리지 진입·청산)은 옵션에 따라 예약주문으로 자동 접수합니다.\n· 09:01 의 시가 확인·갭 취소·원장 대조·매수가능조회·자동 정지는 그대로 작동합니다.\n· 하루 매수 상한을 넘는 계획은 승인하지 않고 포트를 정지합니다.\n\n결과는 매매 로그와 이 화면에서 확인하세요. 계속할까요?")) return;
    setBoBusy(true); setBoMsg("");
    const cap = aaForm.cap.trim() ? priceToApi(market, aaForm.cap) : null;
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/auto-exec/auto-approve`, { method: "PUT",
      body: JSON.stringify({ enabled: aaForm.enabled, market_reserve: aaForm.market_reserve, daily_buy_cap: cap }) });
    const j = (await r.json().catch(() => ({}))) as AutoExec & { detail?: string };
    setBoBusy(false);
    if (!r.ok) { setBoMsg(j.detail ?? `저장 실패 (${r.status})`); return; }
    setBo((prev) => (prev ? { ...prev, auto_exec: j } : prev));
    setAaOpen(false);
    setBoMsg(aaForm.enabled ? "완전 무인 운영을 켰습니다 — 오늘 16:45 부터 자동 승인됩니다" : "완전 무인 운영을 껐습니다 — 이미 승인된 줄은 그대로입니다(필요하면 전량 취소)");
  }
  async function cancelAll(stop: boolean) {
    if (!sum) return;
    const msg = stop
      ? `⛔ 무인 운영을 정지하고 살아 있는 주문 ${liveOrders.length}건(승인·예약·발주)을 모두 취소합니다.\n이미 체결된 주문은 취소할 수 없습니다(반대 매매만 가능). 정지 해제는 배너의 '다시 켜기', 자동 승인은 다시 켜야 합니다.\n\n계속할까요?`
      : `살아 있는 주문 ${liveOrders.length}건(승인·예약·발주)을 모두 취소합니다. 무인 운영 설정은 그대로 둡니다.\n\n계속할까요?`;
    if (!window.confirm(msg)) return;
    setBoBusy(true); setBoMsg("");
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/orders/cancel-all`, { method: "POST", body: JSON.stringify({ stop }) });
    const j = (await r.json().catch(() => ({}))) as { cancelled?: number; failed?: number; filled_untouched?: boolean; detail?: string };
    setBoBusy(false);
    if (!r.ok) { setBoMsg(j.detail ?? `취소 실패 (${r.status})`); return; }
    setBoMsg(`${j.cancelled ?? 0}건 취소${(j.failed ?? 0) > 0 ? ` · 실패 ${j.failed}건 — 표의 메시지를 확인하세요` : ""}${j.filled_untouched ? " · 이미 체결된 줄은 대상 외" : ""}${stop ? " · 무인 운영 정지" : ""}`);
    void load(pid);
  }
  async function cancelOrder(b: BrokerOrderRow) {
    if (!sum || b.id === null) return;
    const what = b.mode === "auto" ? (b.status === "approved" ? "무인 실행 승인" : `무인 발주 #${b.order_no ?? b.id}`) : `예약주문 #${b.rsvn_ord_seq ?? b.id}`;
    if (!window.confirm(`${what} (${b.side === "buy" ? "매수" : "매도"} ${b.qty}주)를 취소할까요?`)) return;
    setBoBusy(true);
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/orders/${b.id}/cancel`, { method: "POST" });
    setBoBusy(false);
    if (!r.ok) setBoMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `취소 실패 (${r.status})`);
    else setBoMsg(b.mode === "auto" ? (b.status === "approved" ? "승인을 철회했습니다" : "발주된 주문을 취소했습니다") : "예약주문을 취소했습니다");
    void load(pid);
  }
  async function refreshOrders() {
    if (!sum) return;
    setBoBusy(true);
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/orders${signal?.exec_day ? `?date=${signal.exec_day}&` : "?"}refresh=1`);
    setBoBusy(false);
    if (r.ok) { setBo((await r.json()) as BrokerOrders); setBoMsg("증권사 상태를 새로 고쳤습니다"); }
  }

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
    let sgj: Signal | null = null;
    if (sg.ok) { sgj = (await sg.json()) as Signal; setSignal(sgj); }
    const jr = await apiFetch(`/portfolio/journal${sid ? `?portfolio_id=${sid}` : ""}`);
    if (jr.ok) setJournal(((await jr.json()) as { items: JournalItem[] }).items);
    if (sid) {
      const bk = await apiFetch(`/portfolio/${sid}/broker`);
      if (bk.ok) setBroker((await bk.json()) as BrokerInfo);
      const bor = await apiFetch(`/portfolio/${sid}/orders${sgj?.exec_day ? `?date=${sgj.exec_day}` : ""}`);
      setBo(bor.ok ? ((await bor.json()) as BrokerOrders) : null);
    }
    // 계좌 목록은 시작 패널(포트가 하나도 없을 때 포함)에서도 쓴다 (2026-09-06)
    const al = await apiFetch("/broker/accounts");
    if (al.ok) setAcctList(((await al.json()) as { items: typeof acctList }).items);
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
      name, market, code_200: market === "KR" ? startCode200 : undefined, etf: market === "US" ? startEtf : undefined,
      credential_id: startAcct ? Number(startAcct) : undefined }) });
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
    setShowStart(false); setNewName(""); setStartCash(""); setStartAcct(""); setAcctBal(null); setAcctMsg("");
    setHoldings([{ code: market === "US" ? "QQQ" : startCode200, qty: "", price: "" }]);
    setPid(id);
  }

  // 시작 패널 '계좌에서 불러오기' (2026-09-06 지시) — D+2 예수금을 현금 칸에, 전략 종목 보유를 보유분 행에 미리 채운다.
  // 자동 확정하지 않는다: 계좌를 일지·다른 포트와 함께 쓰면 예수금 전체가 이 전략 몫이 아닐 수 있어 사용자가 확인·수정 후 시작.
  async function loadAccountBalance() {
    if (!startAcct) return;
    setAcctMsg("조회 중…"); setAcctBal(null);
    const r = await apiFetch(`/broker/accounts/${startAcct}/balance?market=${market}`);
    const j = (await r.json().catch(() => ({}))) as AcctBalance & { detail?: string };
    if (!r.ok) { setAcctMsg(j.detail ?? `조회 실패 (${r.status})`); return; }
    setAcctBal(j); setAcctMsg("");
    setStartCash(String(j.deposit_d2));
    const strat = j.holdings.filter((h) => h.strategy);
    if (strat.length > 0) {
      setStartMode("holdings");
      setHoldings(strat.map((h) => ({ code: h.code, qty: String(h.qty), price: h.avg_price > 0 ? String(h.avg_price) : "" })));
      const c200 = strat.find((h) => h.code === "102110" || h.code === "069500");
      if (c200) setStartCode200(c200.code);  // 주력 조합을 실제 보유 200 ETF 로
    } else {
      setStartMode("fresh");
    }
  }

  // 예수금 대조 (2026-09-06 지시) — 지금 계좌를 조회해 원장 현금과 비교 / 차액을 입출금 한 건으로 등록
  const cc = broker?.linked ? (broker.cash_check ?? null) : null;
  async function refreshCashCheck() {
    if (!sum) return;
    setCcBusy(true); setImpMsg("예수금 대조 중…");
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/cash-check?refresh=true`);
    const j = (await r.json().catch(() => ({}))) as { cash_check?: CashCheck; detail?: string };
    setCcBusy(false);
    if (!r.ok) { setImpMsg(j.detail ?? `대조 실패 (${r.status})`); return; }
    setImpMsg(j.cash_check && j.cash_check.diff === 0 ? "예수금 대조 — 원장 현금과 계좌가 같습니다" : "");
    setBroker((prev) => (prev ? { ...prev, cash_check: j.cash_check ?? null } : prev));
  }
  async function alignCash() {
    if (!sum || !cc || cc.diff === 0) return;
    const kind = cc.diff > 0 ? "입금" : "출금";
    if (!window.confirm(`${kind} ${fm(Math.abs(cc.diff))}을(를) 원장에 등록해 현금을 계좌 D+2 예수금 ${fm(cc.account_cash)}에 맞춥니다.\n\n수수료·분배금·앱 밖 입출금 차액일 때만 사용하세요. 체결 누락이 원인이면 '최근 7일 체결 조회'로 가져오는 것이 맞습니다.`)) return;
    setCcBusy(true);
    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/cash-check/align`, { method: "POST" });
    const j = (await r.json().catch(() => ({}))) as { added?: boolean; amount?: number; detail?: string };
    setCcBusy(false);
    if (!r.ok) { setImpMsg(j.detail ?? `등록 실패 (${r.status})`); return; }
    setImpMsg(j.added ? `${kind} ${fm(j.amount ?? 0)} 등록됨 — 원장 현금이 계좌와 같아졌습니다` : "차이가 없습니다");
    void load(pid);
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
      color: "#f97316", lineWidth: 2, title: "수익률 지수",
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
      <MarketSwitch />

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
            <input type="checkbox" className="accent-[#f97316]" checked={includeCosts} onChange={(e) => setIncludeCosts(e.target.checked)} />
            비용 포함 (추정 수수료)
          </label>
          {sum?.as_of && <span className="text-xs text-faint">기준일 {sum.as_of} · 지연 시세</span>}
          {/* 미국 포트 공식 배지 — 이 포트의 주문표 규칙 (2026-09-06 지시) */}
          {market === "US" && sum && (() => {
            const f = formulaOf(portfolios.find((p) => p.id === sum.portfolio.id)?.etf);
            return (
              <Link href={f.guide} title={`${f.desc} — 가이드 보기`}
                className="rounded-lg border border-accent/40 bg-accent-dim px-2.5 py-1 text-[12.5px] font-semibold text-accent hover:border-accent">
                공식 {f.short}
              </Link>
            );
          })()}
          {/* 이름·배경색 편집 (2026-09-05 지시) — 탭이 많아지면 이름과 색으로 구분 */}
          <button className="rounded-lg border border-line bg-inset px-3 py-1.5 text-[13px] text-muted transition-colors hover:border-accent hover:text-accent"
            onClick={() => {
              if (!sum) return;
              setEditName(sum.portfolio.name);
              setEditColor(portfolios.find((p) => p.id === sum.portfolio.id)?.color ?? "");
              setEditEtf(formulaOf(portfolios.find((p) => p.id === sum.portfolio.id)?.etf).key);
              setEditOpen(!editOpen);
            }}>✏️ 이름·색</button>
          {/* 파괴적 액션은 탭 줄과 분리하되 명확히 보이게 — 확인 대화상자로 이중 안전 (2026-09-02) */}
          <button className="rounded-lg border border-line bg-inset px-3 py-1.5 text-[13px] text-muted transition-colors hover:border-down hover:text-down"
            onClick={() => void deletePortfolio()}>🗑 이 포트 삭제</button>
        </span>
      </div>

      {sum && (
        <Card className="mb-4">
          <CardTitle right={
            <Link href="/settings" className="text-[12.5px] font-normal normal-case text-accent">
              계좌 등록·관리 →</Link>
          }>
            증권사 연동 <span className="normal-case text-faint">
              · 설정에 등록한 계좌를 선택하면 체결을 자동으로 가져오고, 주문표에서 예약주문을 접수할 수 있습니다</span>
          </CardTitle>
          <div className="flex flex-wrap items-center gap-3 text-[13.5px]">
            <select className="input !py-2" value={broker?.linked ? String(broker.id ?? "") : ""}
              onChange={(e) => void (async () => {
                const v = e.target.value;
                const r = await apiFetch(`/portfolio/${sum.portfolio.id}/broker`, {
                  method: "PUT", body: JSON.stringify({ credential_id: v ? Number(v) : null }) });
                if (r.ok) { setImp(null); setImpMsg(""); void load(pid); }
              })()}>
              <option value="">연결 안 함</option>
              {acctList.map((a) => (
                <option key={a.id} value={a.id}>{a.label} ({a.account_no}-{a.acnt_prdt_cd}{a.env === "vps" ? " · 모의" : ""})</option>
              ))}
            </select>
            {acctList.length === 0 && (
              <span className="text-faint">등록된 계좌가 없습니다 — <Link href="/settings" className="text-accent underline underline-offset-2">일반 설정</Link>에서 먼저 등록하세요.</span>
            )}
            {broker?.linked && (
              <>
                <span className="rounded-lg bg-ok/10 px-2.5 py-1 font-semibold text-ok">연결됨</span>
                {broker.last_import_at && <span className="text-faint">마지막 가져오기 {broker.last_import_at.slice(0, 16).replace("T", " ")}</span>}
                {market === "KR" && (
                  <span className="text-[12.5px] text-faint">
                    {cc ? <>예수금 대조 {cc.at.slice(5, 16).replace("T", " ")} · 차이 <b className={cc.diff === 0 ? "text-ok" : cc.warn ? "text-warn" : "text-ink"}>{cc.diff === 0 ? "없음" : fm(cc.diff)}</b></> : "예수금 대조 기록 없음 (15:45 동기화 때 저장)"}
                    <button className="ml-2 text-accent hover:underline disabled:opacity-50" disabled={ccBusy} onClick={() => void refreshCashCheck()}>지금 대조</button>
                    {cc && cc.diff !== 0 && !cc.warn && <button className="ml-2 text-accent hover:underline disabled:opacity-50" disabled={ccBusy} onClick={() => void alignCash()}>차액 등록</button>}
                  </span>
                )}
                <button className="btn !py-1.5 text-[13px]" onClick={() => void (async () => {
                  setImpMsg("조회 중…"); setImp(null);
                  const r = await apiFetch(`/portfolio/${sum.portfolio.id}/import-fills?days=7&dry_run=true`, { method: "POST" });
                  if (r.ok) { setImp(await r.json()); setImpMsg(""); }
                  else setImpMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `실패 (${r.status})`);
                })()}>최근 7일 체결 조회</button>
                {imp && imp.items.length > 0 && (
                  <button className="btn btn-primary !py-1.5 text-[13px]" onClick={() => void (async () => {
                    const r = await apiFetch(`/portfolio/${sum.portfolio.id}/import-fills?days=7&dry_run=false`, { method: "POST" });
                    if (r.ok) { const j = await r.json(); setImp(j); setImpMsg(`${j.added}건 등록됨`); void load(pid); }
                    else setImpMsg(((await r.json().catch(() => ({}))) as { detail?: string }).detail ?? `실패 (${r.status})`);
                  })()}>가져오기 실행</button>
                )}
              </>
            )}
            {impMsg && <span className="text-[13px] text-muted">{impMsg}</span>}
          </div>

          {imp && (
            <div className="mt-3 overflow-x-auto border-t border-line pt-3">
              <div className="mb-1.5 text-[13px] text-muted">
                조회 {imp.items.length}건 · 등록 {imp.added} · 중복 {imp.skipped}
                {imp.unknown_codes.length > 0 && <span className="text-warn"> · 미시딩 {imp.unknown_codes.join(", ")}</span>}
              </div>
              <table className="w-full whitespace-nowrap text-[13px]">
                <thead><tr className="border-b border-line text-left text-[12px] text-faint">
                  <th className="pb-1 font-medium">일자</th><th className="pb-1 font-medium">종목</th>
                  <th className="pb-1 font-medium">구분</th>
                  <th className="pb-1 text-right font-medium">수량</th>
                  <th className="pb-1 text-right font-medium">체결가</th>
                  <th className="pb-1 pl-3 font-medium">상태</th>
                </tr></thead>
                <tbody>
                  {imp.items.map((r, i) => (
                    <tr key={i} className="border-b border-line/50 last:border-0">
                      <td className="py-1.5">{r.date}</td>
                      <td className="py-1.5">{r.name || r.code}</td>
                      <td className={`py-1.5 font-semibold ${r.side === "buy" ? "text-up" : "text-down"}`}>{r.side === "buy" ? "매수" : "매도"}</td>
                      <td className="table-num py-1.5">{r.qty.toLocaleString()}</td>
                      <td className="table-num py-1.5">{r.price.toLocaleString()}</td>
                      <td className="py-1.5 pl-3 text-faint">{r.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {editOpen && sum && (
        <Card className="mb-4 max-w-xl border-accent">
          <CardTitle>포트 이름 · 탭 배경색{market === "US" ? " · 매매 공식" : ""}</CardTitle>
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
            {market === "US" && (
              <div className="grid gap-1 text-[13px] text-faint">매매 공식
                <div className="grid gap-2 sm:grid-cols-3">
                  {US_FORMULAS.map((f) => (
                    <button key={f.key} onClick={() => setEditEtf(f.key)}
                      className={`rounded-xl border p-3 text-left text-[13.5px] transition-colors ${editEtf === f.key ? "border-accent bg-accent-dim" : "border-line bg-inset hover:border-line-strong"}`}>
                      <div className="font-bold text-ink">{f.label}</div>
                      <div className="mt-0.5 text-[12px] leading-snug text-faint">{f.desc}</div>
                    </button>
                  ))}
                </div>
                <span className="text-[12px] text-faint">공식을 바꾸면 보유는 그대로 두고 <b className="text-muted">다음 주문표부터</b> 새 규칙으로 목표 비중에 맞추는 주문이 나옵니다.</span>
              </div>
            )}
            <div className="flex items-center gap-3">
              <button className="btn btn-primary" onClick={() => void (async () => {
                const r = await apiFetch(`/portfolios/${sum.portfolio.id}`, {
                  method: "PATCH", body: JSON.stringify({ name: editName.trim(), color: editColor, etf: market === "US" ? editEtf : undefined }) });
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
            {/* 증권사 계좌 — 고르면 시작과 함께 연결. KR 은 잔고를 불러와 입금·보유분을 미리 채운다 (2026-09-06 지시) */}
            <div className="flex flex-wrap items-end gap-3 text-[13.5px]">
              <label className="grid gap-1 text-[13px] text-faint">증권사 계좌 (선택)
                <select className="input !py-2" value={startAcct}
                  onChange={(e) => { setStartAcct(e.target.value); setAcctBal(null); setAcctMsg(""); }}>
                  <option value="">연결 안 함</option>
                  {acctList.map((a) => (
                    <option key={a.id} value={a.id}>{a.label} ({a.account_no}-{a.acnt_prdt_cd}{a.env === "vps" ? " · 모의" : ""})</option>
                  ))}
                </select></label>
              {startAcct && market === "KR" && (
                <button className="btn !py-2" onClick={() => void loadAccountBalance()}>계좌에서 불러오기</button>
              )}
              {startAcct && market === "US" && <span className="text-faint">미국 계좌의 잔고 불러오기는 지원하지 않습니다 — 계좌 연결만 됩니다.</span>}
              {acctList.length === 0 && <span className="text-faint">등록된 계좌가 없습니다 — <Link href="/settings" className="text-accent underline underline-offset-2">일반 설정</Link>에서 등록하면 잔고를 불러올 수 있습니다.</span>}
              {acctMsg && <span className="text-muted">{acctMsg}</span>}
            </div>
            {acctBal && (
              <div className="rounded-lg border border-line bg-inset px-3 py-2 text-[12.5px] leading-relaxed text-muted">
                📥 계좌 잔고 불러옴 — D+2 예수금 <b className="text-ink">{fm(acctBal.deposit_d2)}</b>
                <span className="text-faint"> (예수금총액 {fm(acctBal.deposit)} · 총평가 {fm(acctBal.total_eval)})</span>
                {" · "}보유 {acctBal.holdings.length}종목 중 전략 종목 <b className="text-ink">{acctBal.strategy_count}</b>개를 보유분에 채움
                {acctBal.holdings.some((h) => !h.strategy) && (
                  <span className="text-faint"> · 전략 외 제외: {acctBal.holdings.filter((h) => !h.strategy).map((h) => `${h.name || h.code} ${h.qty}주`).join(", ")}</span>
                )}
                <div className="mt-0.5 text-faint">아래 값은 확인·수정 후 시작하세요. 이 계좌를 매매일지나 다른 포트와 함께 쓰면 예수금 전체가 이 전략의 몫이 아닐 수 있습니다.</div>
              </div>
            )}
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
            {market === "US" && (
              <div className="mb-1 grid gap-1.5 text-[13.5px]">
                <span className="font-semibold text-muted">매매 공식 <span className="font-normal text-faint">— 이 포트의 주문표가 이 규칙으로 계산됩니다 · 나중에 이름·색 편집에서 바꿀 수 있습니다</span></span>
                <div className="grid gap-2 sm:grid-cols-3">
                  {US_FORMULAS.map((f) => (
                    <button key={f.key} onClick={() => setStartEtf(f.key)}
                      className={`rounded-xl border p-3 text-left transition-colors ${startEtf === f.key ? "border-accent bg-accent-dim" : "border-line bg-inset hover:border-line-strong"}`}>
                      <div className="font-bold">{f.label}</div>
                      <div className="mt-0.5 text-[12.5px] leading-snug text-faint">{f.desc}</div>
                    </button>
                  ))}
                </div>
                <span className="text-[12px] text-faint">어떤 공식을 고를지 모르겠다면 <Link href="/guide" className="text-accent underline underline-offset-2">가이드 · 매매 공식 개요</Link>를 보세요.</span>
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
      {/* 상단 현황판 — 카드 8장(2줄 반)을 핵심 3장으로 압축, 나머지는 카드 안 보조 줄로 (2026-09-05 지시).
          총자산(현금·주식 구성) · 순손익(실현·평가 세부) · 수익률 TWR(XIRR 보조). 그래프는 바로 아래. */}
      {sum && (
        <div className="mb-3 grid gap-3 md:grid-cols-3">
          {/* 카드 위계 규칙 (2026-09-06): 총자산만 핵심 카드, 순손익·수익률은 19px */}
          <Stat hero label="총자산" value={fm(sum.total_equity)} spark={curve.map((c) => c.equity)}
            tip={<span>현금 + 보유 주식 평가액(최근 종가 기준)의 합.<br />현금 = 입금 − 출금 − 매수금액 + 매도금액의 원장 잔액, 주식 = 보유 수량 × 최근 종가(지연 시세).</span>}
            sub={<span className="flex flex-wrap gap-x-3">
              <span>현금 <b className="text-ink">{fm(sum.cash)}</b></span>
              <span>주식 <b className="text-ink">{fm(sum.stock_value)}</b>{sum.total_equity > 0 && <span className="text-faint"> ({(sum.stock_value / sum.total_equity * 100).toFixed(0)}%)</span>}</span>
            </span>} />
          <Stat label={`순손익${includeCosts ? " (비용차감)" : ""}`} value={withPct(fm(net), netPct)} tone={pnlTone(net)}
            // 손익 추이(평가액 − 납입 원금, 비용 차감 전) — 세 카드 모두 하단 미니 그래프로 일관 (2026-09-05 지시)
            spark={curve.map((c) => c.pnl ?? 0)} sparkColor={net > 0 ? "#d92f45" : net < 0 ? "#2563eb" : "#9aa1ad"}
            tip={<span>실현손익 + 평가손익 − 추정 수수료(체크 시). %는 납입 원금(입금−출금) 대비 — 원금 이상 출금 시 %는 표시하지 않습니다.<br />
              실현손익 = 매도로 확정된 손익(FIFO 매칭), 평가손익 = (현재가 − 평균단가) × 보유 수량(%는 보유원가 대비).</span>}
            sub={<span className="flex flex-wrap gap-x-3">
              <span>실현 <b className={toneCls[pnlTone(sum.realized_pnl)]}>{fm(sum.realized_pnl)}</b></span>
              <span>평가 <b className={toneCls[pnlTone(sum.unrealized_pnl)]}>{withPct(fm(sum.unrealized_pnl), evalPct)}</b></span>
            </span>} />
          <Stat label="수익률 (TWR)" value={fmtPct(sum.twr, 2)} tone={pnlTone(sum.twr ?? 0)} spark={curve.map((c) => c.index)} sparkColor="#2a78d6"
            tip={<span>시간가중수익률 — 입출금 시점의 영향을 제거한 운용 성과. 펀드 수익률과 같은 방식이며, 입금이 많아도 왜곡되지 않습니다.<br />
              XIRR = 내부수익률(연환산) — 입출금 현금흐름과 현재 평가액으로 계산한 &apos;내 돈 기준&apos; 연 수익률.</span>}
            sub={<span>XIRR(연환산) <b className="text-ink">{fmtPct(sum.xirr, 2)}</b></span>} />
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
          <div ref={eqRef} className="h-52" />
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

      {/* 보유 종목 — 현황판·추이 그래프 아래 (2026-09-05 재배치) */}
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



      {/* 오늘의 주문표 (2026-08-28 지시 — 실전매매 중간 섹션) */}
      <Card className="mb-4">
        <CardTitle>
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
        {/* 계획 vs 등록 체결 대조 경고 — 표시만, 자동 수정 없음 (2026-09-05 지시) */}
        {(signal?.reconcile?.items?.length ?? 0) > 0 && (
          <div className="mb-3 rounded-lg border border-warn/40 bg-warn/5 px-3.5 py-2.5">
            <div className="mb-1 text-[13px] font-bold text-warn">
              ⚠️ {signal!.reconcile!.date} 계획과 등록된 거래가 다릅니다 — 확인해 주세요
            </div>
            <ul className="grid gap-0.5 text-[13px] text-muted">
              {signal!.reconcile!.items.map((it, i) => (
                <li key={i}>{it.level === "warn" ? "•" : "·"} {it.text}</li>
              ))}
            </ul>
            <div className="mt-1 text-[11.5px] text-faint">자동으로 고치지 않습니다 — 일지에서 직접 수정하거나 증권사 내역을 가져오세요.</div>
          </div>
        )}
        {/* 예수금 대조 경고 (2026-09-06 지시) — 원장 현금 vs 계좌 D+2 예수금, 허용 오차 초과 시. 자동 수정 없음, 차액 등록은 버튼으로 */}
        {market === "KR" && cc?.warn && (
          <div className="mb-3 rounded-lg border border-warn/40 bg-warn/5 px-3.5 py-2.5 text-[13px]">
            <div className="font-bold text-warn">⚠️ 계좌 예수금과 원장 현금이 다릅니다 — 차이 {fm(cc.diff)}</div>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-muted">
              <span>원장 현금 <b className="text-ink">{fm(cc.ledger_cash)}</b> · 계좌 D+2 예수금 <b className="text-ink">{fm(cc.account_cash)}</b>
                <span className="text-faint"> · 대조 {cc.at.slice(5, 16).replace("T", " ")}</span></span>
              <button className="btn !py-1" disabled={ccBusy} onClick={() => void alignCash()}>차액을 입출금으로 등록</button>
              <button className="btn-ghost btn !py-1" disabled={ccBusy} onClick={() => void refreshCashCheck()}>지금 다시 대조</button>
            </div>
            <div className="mt-1 text-[11.5px] text-faint">자동으로 고치지 않습니다 — 앱 밖 입출금·수수료·분배금이 원인이면 차액 등록으로 맞추고, 체결 누락이면 아래 증권사 연동에서 체결을 가져오세요. 주문표는 원장 현금으로 계산되므로 차이가 크면 매수 수량이 실제와 어긋납니다.</div>
          </div>
        )}
        {signal?.snapshot_missing && (
          <p className="mb-2 text-[12.5px] text-faint">ⓘ 장 마감 배치 스냅샷이 아직 없어 시세로 직접 계산한 주문표입니다 — 배치(16:05) 이후 확정 표기로 바뀝니다.</p>
        )}
        {/* 무인 실행 정지 배너 (ADR-008 ⑥) — 사유 확인 후 사용자가 다시 켠다 */}
        {market === "KR" && ae?.paused && (
          <div className="mb-3 rounded-lg border border-down/40 bg-down/5 px-3.5 py-2.5 text-[13px]">
            <div className="font-bold text-down">⏸ 무인 실행 정지 — {ae.paused_reason}</div>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-muted">
              <span>{ae.paused_at ? `${ae.paused_at.slice(0, 16).replace("T", " ")}부터 ` : ""}승인·발주가 멈춰 있습니다. 계좌와 기록을 맞춘 뒤 다시 켜세요.</span>
              <button className="btn !py-1" disabled={boBusy} onClick={() => void resumeAutoExec()}>다시 켜기</button>
            </div>
          </div>
        )}
        {/* 장 시작 전 예상 시가 갭 취소 결과 (2026-09-06) — 08:57 예상체결가 vs 갭 기준, 취소 건수 */}
        {market === "KR" && bo?.preopen?.last_run && bo.preopen.last_run.date === (signal?.exec_day ?? "") && (() => { const p = bo.preopen!.last_run!; return (
          <p className="mb-2 text-[12.5px] text-muted">
            🕗 장 시작 전 갭 확인 {p.at.slice(11, 16)} —{" "}
            {p.expected == null ? <span className="text-faint">예상체결가 없음{p.note ? ` (${p.note})` : ""}</span>
              : p.gap_hit ? <>예상 시가 {fpx(p.expected)} ≤ 기준 {fpx(p.gap_exact ?? 0)} → <b className="text-down">그리드 매수 {p.cancelled}건 취소</b>
                  {p.failed > 0 && <> · <b className="text-down">취소 실패 {p.failed}건</b></>}
                  {p.unmatched > 0 && <> · 취소 불가 {p.unmatched}건(미체결 목록에 없음)</>}
                  {p.untracked > 0 && <span className="text-faint"> · 앱 밖 주문 {p.untracked}건 포함</span>}
                  {p.note && <span className="text-faint"> · {p.note}</span>}</>
              : <>예상 시가 {fpx(p.expected)} {">"} 기준 {fpx(p.gap_exact ?? 0)} — 그리드 매수 유지{p.note ? ` · ${p.note}` : ""}</>}
          </p>
        ); })()}
        {market === "KR" && ae?.last_run && ae.last_run.date === (signal?.exec_day ?? "") && (
          <p className="mb-2 text-[12.5px] text-muted">
            🤖 무인 실행 {ae.last_run.at.slice(11, 16)} — 발주 <b className="text-ink">{ae.last_run.submitted}</b>건
            {ae.last_run.skipped_gap > 0 && <> · 갭 취소 생략 <b className="text-ink">{ae.last_run.skipped_gap}</b>건</>}
            {ae.last_run.skipped > 0 && <> · 생략 <b className="text-ink">{ae.last_run.skipped}</b>건</>}
            {ae.last_run.failed > 0 && <> · <b className="text-down">실패 {ae.last_run.failed}건</b></>}
            {ae.last_run.open != null && <span className="text-faint"> · 시가 {fpx(ae.last_run.open)}{ae.last_run.gap_hit ? " (갭 취소 발동)" : ""}</span>}
          </p>
        )}
        {/* 완전 무인 운영 (2026-09-07 지시) — 자동 승인 상태·설정, 살아 있는 주문 전량 취소(긴급 정지) */}
        {market === "KR" && broker?.linked && ae && (
          <div className="mb-3 rounded-lg border border-line bg-inset px-3.5 py-2.5 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-ink">🤖 완전 무인 운영</span>
              <span className={`rounded-md px-2 py-0.5 text-[12px] font-semibold ${aa.enabled ? "bg-accent-dim text-accent" : "bg-raised text-faint"}`}>{aa.enabled ? "켜짐" : "꺼짐"}</span>
              <span className="text-muted">
                {aa.enabled
                  ? `16:45 주문표 자동 승인${aa.market_reserve ? " · 시장가 줄은 예약주문 자동 접수" : " · 시장가 줄은 수동"}${aa.daily_buy_cap ? ` · 하루 매수 상한 ${fm(aa.daily_buy_cap)}` : ""}`
                  : "장 마감 후 주문표를 자동 승인해 매일 승인 없이 09:01 에 발주하려면 켜세요"}
              </span>
              <button className="btn !py-1" disabled={boBusy} onClick={() => setAaOpen((o) => !o)}>{aaOpen ? "닫기" : "설정"}</button>
              {liveOrders.length > 0 && (
                <>
                  <button className="btn !py-1" disabled={boBusy} onClick={() => void cancelAll(false)}>전량 취소 ({liveOrders.length})</button>
                  <button className="btn !py-1 !text-down" disabled={boBusy} onClick={() => void cancelAll(true)}>⛔ 무인 중지 + 전량 취소</button>
                </>
              )}
            </div>
            {ae.auto_approve_last && (
              <div className="mt-1 text-[12.5px] text-faint">
                마지막 자동 승인 {ae.auto_approve_last.at.slice(5, 16).replace("T", " ")} — 실행일 {ae.auto_approve_last.exec_day} · 지정가 승인 <b className="text-ink">{ae.auto_approve_last.approved}</b>건
                {ae.auto_approve_last.reserved > 0 && <> · 시장가 예약 <b className="text-ink">{ae.auto_approve_last.reserved}</b>건</>}
                {ae.auto_approve_last.failed > 0 && <> · <b className="text-down">접수 실패 {ae.auto_approve_last.failed}건</b></>}
                {ae.auto_approve_last.manual.length > 0 && <> · 수동 필요 {ae.auto_approve_last.manual.length}건 ({ae.auto_approve_last.manual.slice(0, 2).join(", ")})</>}
                {ae.auto_approve_last.note && <> · {ae.auto_approve_last.note}</>}
              </div>
            )}
            {aaOpen && (
              <div className="mt-2 grid gap-2 border-t border-line pt-2">
                <label className="flex items-center gap-2"><input type="checkbox" className="h-4 w-4 accent-[#c2410c]" checked={aaForm.enabled} onChange={(e) => setAaForm({ ...aaForm, enabled: e.target.checked })} />
                  <span><b className="text-ink">자동 승인 켬</b> <span className="text-faint">— 16:45 에 다음 실행일 주문표를 계산해 허용된 방향의 지정가 줄을 승인(09:01 발주)</span></span></label>
                <label className="flex items-center gap-2"><input type="checkbox" className="h-4 w-4 accent-[#c2410c]" checked={aaForm.market_reserve} onChange={(e) => setAaForm({ ...aaForm, market_reserve: e.target.checked })} />
                  <span><b className="text-ink">시장가 줄은 예약주문으로 자동 접수</b> <span className="text-faint">— 레버리지 진입·청산. 끄면 그 줄은 수동(로그에 '수동 필요')</span></span></label>
                <label className="flex flex-wrap items-center gap-2">
                  <span><b className="text-ink">하루 매수 상한</b> <span className="text-faint">({unit}, 비우면 없음) — 계획 매수 합계가 넘으면 승인하지 않고 정지</span></span>
                  <input className="input w-44 !py-1.5" placeholder="예: 20000000" value={aaForm.cap} onChange={(e) => setAaForm({ ...aaForm, cap: e.target.value })} /></label>
                <div className="flex flex-wrap items-center gap-2">
                  <button className="btn btn-primary !py-1.5" disabled={boBusy} onClick={() => void saveAutoApprove()}>저장</button>
                  <span className="text-[12px] text-faint">설정 › 무인 실행의 매수·매도 허용이 켜진 방향만 승인됩니다. 정지 상태에서는 자동 승인도 멈춥니다.</span>
                </div>
              </div>
            )}
          </div>
        )}
        {/* 예약주문 접수 (2026-09-05 지시) — 장 마감 후 버튼으로 KIS 예약주문, 사용자가 확인한 뒤에만 */}
        {market === "KR" && signal?.status === "OK" && signal.orders && signal.orders.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 text-[13px]">
            {broker?.linked ? (
              <>
                <button className="btn btn-primary !py-1.5" disabled={boBusy || !bo?.window.open || selectedLines.length === 0}
                  title={!bo?.window.open ? (bo?.window.reason ?? "") : pendingLines.length === 0 ? "모든 줄이 이미 접수되었습니다"
                    : selectedLines.length === 0 ? "표에서 등록할 줄을 체크하세요" : ""}
                  onClick={() => setBoConfirm(true)}>
                  선택 주문 등록하기{selectedLines.length > 0 ? ` (${selectedLines.length}/${pendingLines.length}건)` : pendingLines.length > 0 ? ` (0/${pendingLines.length}건)` : ""}
                </button>
                {/* 무인 실행 승인 (2026-09-06, ADR-008) — 설정에서 허용한 방향의 지정가 줄만 */}
                {ae && (ae.allowed.buy || ae.allowed.sell) && (
                  <button className="btn !py-1.5" disabled={boBusy || aeEligible.length === 0 || ae.paused}
                    title={ae.paused ? "무인 실행이 정지되어 있습니다 — 배너에서 다시 켜세요"
                      : aeEligible.length === 0 ? (selectedLines.length === 0 ? "표에서 승인할 줄을 체크하세요" : "선택한 줄이 모두 무인 실행 대상이 아닙니다 (시장가 또는 허용 안 된 방향)")
                      : "선택한 지정가 줄을 실행일 09:01 시가 확인 후 자동 발주"}
                    onClick={() => setAeConfirm(true)}>
                    🤖 무인 실행 승인{aeEligible.length > 0 ? ` (${aeEligible.length}건)` : ""}
                  </button>
                )}
                {pendingLines.length > 0 && (
                  <button className="text-[12.5px] text-accent hover:underline" disabled={boBusy}
                    onClick={() => setSel(selectedLines.length === pendingLines.length ? new Set() : new Set(pendingLines.map(lineKey)))}>
                    {selectedLines.length === pendingLines.length ? "전체 해제" : "전체 선택"}
                  </button>
                )}
                <span className={bo?.window.open ? "text-faint" : "text-warn"}>{bo?.window.reason ?? ""}</span>
                {bo && bo.items.some((i) => ACTIVE.includes(i.status)) && (
                  <button className="btn !py-1.5" disabled={boBusy} onClick={() => void refreshOrders()}>증권사 상태 새로고침</button>
                )}
              </>
            ) : (
              <span className="text-faint">예약주문을 접수하려면 아래 <b className="text-ink">증권사 연동</b>에서 계좌를 연결하세요.</span>
            )}
            {boMsg && <span className="text-muted">{boMsg}</span>}
          </div>
        )}
        {aeConfirm && signal?.orders && ae && (
          <div className="mb-3 rounded-lg border border-accent/40 bg-accent-dim/40 p-3.5 text-[13px]">
            <div className="mb-2 text-[13.5px] font-bold">
              🤖 무인 실행 승인 {aeEligible.length}건 — {broker?.label} ({broker?.account_no}-{broker?.acnt_prdt_cd}) · 실행일 {signal.exec_day} 09:01
            </div>
            <ul className="mb-2 grid gap-1">
              {aeEligible.map((o, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2">
                  <Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{ORDER_KIND_KO[o.kind] ?? o.kind}</Badge>
                  <span>{instName(o)}</span>
                  <b className={o.side === "buy" ? "text-up" : "text-down"}>{o.side === "buy" ? "매수" : "매도"}</b>
                  <span className="table-num">{o.qty.toLocaleString()}주</span>
                  <span className="table-num">지정가 {fpx(o.price ?? 0)}</span>
                  <span className="text-faint">≈ {fm(o.qty * (o.price ?? 0))}</span>
                </li>
              ))}
            </ul>
            {aeBlocked.length > 0 && (
              <p className="mb-2 text-warn">선택 중 {aeBlocked.length}건은 제외됩니다 — 시장가 줄이거나 설정에서 그 방향의 무인 실행이 꺼져 있습니다. 필요하면 예약주문으로 접수하세요.</p>
            )}
            <p className="mb-2 leading-relaxed text-muted">
              실행일 <b className="text-ink">09:01</b>에 당일 시가를 확인합니다. 시가가 갭 취소 기준{signal.gap_cancel_below ? `(${fpx(signal.gap_cancel_below)})` : ""} 이하면
              그리드 매수는 발주하지 않고 생략으로 기록합니다. 그 외 줄은 위 지정가로 정규 주문을 냅니다.
              예수금·잔고가 부족하면 발주하지 않고, 발주가 2회 연속 실패하거나 장 마감 대조에서 불일치가 나오면 이 포트의 무인 실행은 자동으로 멈춥니다.
              승인은 실행일 09:00 전까지 아래 표에서 취소할 수 있습니다.
            </p>
            <div className="flex gap-2">
              <button className="btn btn-primary !py-1.5" disabled={boBusy || aeEligible.length === 0} onClick={() => void approveSelected()}>
                {boBusy ? "승인 중…" : `${aeEligible.length}건 승인하기`}</button>
              <button className="btn !py-1.5" disabled={boBusy} onClick={() => setAeConfirm(false)}>닫기</button>
            </div>
          </div>
        )}
        {boConfirm && signal?.orders && (
          <div className="mb-3 rounded-lg border border-line-strong bg-raised/40 p-3.5 text-[13px]">
            <div className="mb-2 text-[13.5px] font-bold">
              선택한 {selectedLines.length}건 예약주문 등록 — {broker?.label} ({broker?.account_no}-{broker?.acnt_prdt_cd}) · 실행일 {signal.exec_day}
            </div>
            <ul className="mb-2 grid gap-1">
              {selectedLines.map((o, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2">
                  <Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{ORDER_KIND_KO[o.kind] ?? o.kind}</Badge>
                  <span>{instName(o)}</span>
                  <b className={o.side === "buy" ? "text-up" : "text-down"}>{o.side === "buy" ? "매수" : "매도"}</b>
                  <span className="table-num">{o.qty.toLocaleString()}주</span>
                  <span className="table-num">{o.price ? `지정가 ${fpx(o.price)}` : "시장가"}</span>
                  {o.price && <span className="text-faint">≈ {fm(o.qty * o.price)}</span>}
                </li>
              ))}
            </ul>
            <p className="mb-2 text-faint">
              {(() => {
                const buy = selectedLines.filter((o) => o.side === "buy" && o.price).reduce((a, o) => a + o.qty * (o.price ?? 0), 0);
                const sell = selectedLines.filter((o) => o.side === "sell" && o.price).reduce((a, o) => a + o.qty * (o.price ?? 0), 0);
                return `지정가 합계 — 매수 ${fm(buy)} · 매도 ${fm(sell)}. `;
              })()}
              장 시작 시 KIS 가 주문합니다(예약주문은 당일만 유효). 접수 후에도 아래 표에서 줄별로 취소할 수 있습니다. 시장가 줄은 시가로 체결됩니다.
            </p>
            <div className="flex gap-2">
              <button className="btn btn-primary !py-1.5" disabled={boBusy || selectedLines.length === 0} onClick={() => void reserveSelected()}>
                {boBusy ? "등록 중…" : `${selectedLines.length}건 등록하기`}</button>
              <button className="btn !py-1.5" disabled={boBusy} onClick={() => setBoConfirm(false)}>닫기</button>
            </div>
          </div>
        )}
        {signal?.status === "OK" && signal.orders && signal.orders.length > 0 ? (
          <div className="overflow-x-auto">
            {/* 모바일: 줄바꿈 금지 + 축약(종목 짧게·작은 글씨)으로 한 화면에 — 넘치면 가로 스크롤 (2026-09-02 지시) */}
            <table className="w-full whitespace-nowrap text-[13px] sm:text-[14.5px]">
              <thead><tr className="border-b border-line text-left text-[13px] text-faint">
                {market === "KR" && broker?.linked && (
                  <th className="pb-2 pr-1 font-medium">
                    <input type="checkbox" className="h-4 w-4 accent-[var(--color-ink)]" title="접수 대기 줄 전체 선택/해제"
                      disabled={pendingLines.length === 0}
                      checked={pendingLines.length > 0 && selectedLines.length === pendingLines.length}
                      onChange={(e) => setSel(e.target.checked ? new Set(pendingLines.map(lineKey)) : new Set())} />
                  </th>
                )}
                <th className="pb-2 font-medium">구분</th><th className="pb-2 font-medium">종목</th>
                <th className="pb-2 font-medium">방향</th>
                <th className="pb-2 text-right font-medium">방식 · 가격</th>
                <th className="pb-2 text-right font-medium">수량<span className="hidden sm:inline">{signal?.basis === "portfolio" ? " (내 계좌 기준)" : " (모델 1억)"}</span></th>
                <th className="pb-2 pl-4 font-medium">체결</th>
                {market === "KR" && broker?.linked && <th className="pb-2 pl-3 font-medium">예약</th>}
              </tr></thead>
              <tbody>
                {signal.orders.map((o, i) => (
                  <tr key={i} className={`border-b border-line/50 last:border-0 ${market === "KR" && broker?.linked && !sel.has(lineKey(o)) && pendingLines.includes(o) ? "opacity-60" : ""}`}>
                    {market === "KR" && broker?.linked && (
                      <td className="py-2 pr-1">
                        {pendingLines.includes(o) ? (
                          <input type="checkbox" className="h-4 w-4 accent-[var(--color-ink)]" checked={sel.has(lineKey(o))} onChange={() => toggleSel(o)} />
                        ) : (
                          <span className="inline-block h-4 w-4 text-center text-[12px] text-faint" title="이미 접수됨">✓</span>
                        )}
                      </td>
                    )}
                    <td className="py-2"><Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{ORDER_KIND_KO[o.kind] ?? o.kind}</Badge></td>
                    <td className="py-2">
                      {(() => {
                        const full = o.instrument === "K200" ? (signal?.name_200 ?? (market === "US" ? "QQQ" : "KODEX 200")) : (signal?.name_lev ?? (market === "US" ? "레버리지(QLD/TQQQ)" : "KODEX 레버리지"));
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
                    {market === "KR" && broker?.linked && (
                      <td className="py-2 pl-3 text-[12.5px]">
                        {(() => {
                          const b = boFor(o);
                          if (!b) return <span className="text-faint">—</span>;
                          if (b.status === "reserved") return (
                            <span className="inline-flex items-center gap-1.5" title={`예약주문 #${b.rsvn_ord_seq ?? ""} · ${b.message ?? ""}`}>
                              <span className="font-semibold text-ok">✓ 등록완료</span>
                              <span className="text-faint">#{b.rsvn_ord_seq}</span>
                              <button className="text-faint hover:text-down" disabled={boBusy} onClick={() => void cancelOrder(b)}>취소</button>
                            </span>);
                          if (b.status === "approved") return (
                            <span className="inline-flex items-center gap-1.5" title={b.message ?? ""}>
                              <span className="font-semibold text-accent">🤖 무인 승인</span>
                              <button className="text-faint hover:text-down" disabled={boBusy} onClick={() => void cancelOrder(b)}>취소</button>
                            </span>);
                          if (b.status === "submitted") return (
                            <span className="inline-flex items-center gap-1.5" title={b.message ?? ""}>
                              <span className="font-semibold text-ok">🤖 발주됨</span>
                              <span className="text-faint">#{b.order_no}</span>
                              <button className="text-faint hover:text-down" disabled={boBusy} onClick={() => void cancelOrder(b)}>취소</button>
                            </span>);
                          if (b.status === "filled") return <span className="font-semibold text-ok" title={`주문번호 ${b.order_no ?? ""}`}>✓ 체결 {b.filled_qty.toLocaleString()}주</span>;
                          if (b.status === "partial") return (
                            <span className="inline-flex items-center gap-1.5">
                              <span className="font-semibold text-warn">◐ 일부 체결 {b.filled_qty}/{b.qty}</span>
                              <button className="text-faint hover:text-down" disabled={boBusy} onClick={() => void cancelOrder(b)}>취소</button>
                            </span>);
                          if (b.status === "unfilled") return <span className="text-faint">○ 미체결</span>;
                          if (b.status === "gap_cancelled") return <span className="text-warn" title={b.message ?? ""}>⤫ 갭 취소됨 (예상 시가)</span>;
                          if (b.status === "cancelled") return <span className="text-faint">취소됨</span>;
                          return <span className="text-down" title={b.message ?? ""}>✗ {b.status_ko}{b.message ? ` — ${b.message.slice(0, 40)}` : ""}</span>;
                        })()}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {signal.gap_cancel_below && (
              <p className="mt-2 text-[13px] text-faint">⚠️ 시가 {fpx(signal.gap_cancel_below)} 이하 출발 시 그리드 전량 취소</p>
            )}
            {/* 계산 근거 — 구 주문표 페이지 이관 (2026-09-05): 주문별 실행 조건 + 지표값 */}
            <details className="mt-3 border-t border-line pt-2">
              <summary className="cursor-pointer text-[13px] font-semibold text-muted">▸ 계산 근거 (실행 조건 · 지표값)</summary>
              <div className="mt-2 grid gap-4 lg:grid-cols-2">
                <div className="grid gap-1 text-[13px] text-muted">
                  {signal.orders!.map((o, i) => {
                    const desc = orderCondDesc(o, signal.indicators);
                    return desc ? (
                      <div key={i}><b className="text-ink">{ORDER_KIND_KO[o.kind] ?? o.kind}</b> — {desc}</div>
                    ) : null;
                  })}
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[13px]">
                  {IND_LABELS.map(([key, label, kind]) => {
                    const v = signal.indicators?.[key];
                    if (v === undefined || v === null) return null;
                    return (
                      <span key={key} className="flex justify-between gap-2 text-muted">
                        <span>{label}</span>
                        <b className="text-ink">{kind === "price" ? fpx(Math.round(v)) : `${(v * 100).toFixed(2)}%`}</b>
                      </span>
                    );
                  })}
                </div>
              </div>
            </details>
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
