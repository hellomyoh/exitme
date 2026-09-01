"use client";

/**
 * 주문표 — 기준(모델 포트) 설명 + 내 투자금 환산 + 조건 설명 열 + 레짐 도움말 (2026-08-28 재설계).
 * 주문표의 기준: 시딩 시작일부터 전략을 그대로 따라온 "모델 포트폴리오(초기 1억)"의 현재 상태.
 */
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiFetch, ensureSession } from "../../lib/api";
import { fmtMoneyM, fmtPriceM, marketOf, MARKET_LABEL } from "../../lib/market";
import { Badge, Callout, Card, CardTitle, EmptyState, fmtNum, fmtPct, GaugeBar, PageTitle, RegimeTip, Tip } from "../../components/ui";

type OrderRow = { instrument: string; side: string; otype: string; qty: number; price: number | null; kind: string };
type Signal = {
  status: string; strategy?: string; reason?: string; trade_date?: string; version?: number; regime?: string;
  e_target?: number; w_200?: number; w_lev?: number; gap_cancel_below?: number;
  indicators?: Record<string, number>; orders?: OrderRow[];
  basis?: "model" | "portfolio";
  account?: { cash: number; qty_200: number; qty_lev: number; equity: number };
  detail?: { model_capital?: number; model_equity?: number; model_cash?: number;
             model_qty_200?: number; model_qty_lev?: number; error?: string };
};
type PortfolioItem = { id: number; name: string };
type JournalOrder = { instrument: string; side: string; kind: string; price: number | null; qty: number };
type JournalDay = {
  date: string; regime: string; equity: number; day_return: number; day_pnl: number;
  qty_200: number; qty_lev: number; cash: number; planned: JournalOrder[]; fills: JournalOrder[];
};

type PortfolioTx = {
  id: number; kind: string; code: string | null; name: string | null;
  qty: number | null; price: number | null; amount: number | null;
  realized_pnl: number | null; executed_at: string; memo: string | null;
};
// 시작 시드(보유분 등록·백테스트 이관)는 매매 이력이 아님 — 이력 섹션에서 제외
const SEED_MEMOS = new Set(["보유분 등록", "백테스트 보유분 이관"]);

const REGIME = {
  BULL: { ko: "상승장", color: "var(--color-up)", tone: "up" as const, desc: "그리드로 사되 익절 없이 보유(코어) — 추세를 끝까지 탑니다. 레버리지 허용" },
  NEUTRAL: { ko: "중립장", color: "var(--color-accent)", tone: "accent" as const, desc: "그리드 왕복 — 떨어지면 사고, +Grid% 오르면 익절 (횡보 차익)" },
  BEAR: { ko: "하락장", color: "var(--color-down)", tone: "down" as const, desc: "신규 매수 정지 · 보유 축소 · 레버리지 청산" },
};
const KIND_KO: Record<string, string> = {
  grid1: "그리드 1차", grid2: "그리드 2차", grid3: "그리드 3차", tp: "익절",
  reduce: "축소", lev_strat: "레버리지 전략", lev_tact1: "레버리지 전술 1차",
  lev_tact2: "레버리지 전술 2차", lev_tact_exit: "전술 이탈", lev_liq: "레버리지 청산",
};
const NAME_KR: Record<string, string> = { K200: "KODEX 200", LEV: "KODEX 레버리지" };
const NAME_US: Record<string, string> = { K200: "QQQ", LEV: "레버리지 (QLD/TQQQ)" };

function orderDesc(o: OrderRow, sig: Signal): string {
  const grid = sig.indicators?.grid ?? 0;
  const close = sig.indicators?.close ?? 0;
  if (o.kind.startsWith("grid")) {
    const k = Number(o.kind.slice(4));
    return `전일 종가 ${fmtNum(close)}원에서 −${(grid * k * 100).toFixed(1)}% 하락 시 매수`;
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
  };
  return map[o.kind] ?? "";
}

function indRows(base: string, price: (v: number) => string): { key: string; label: string; fmt: (v: number) => string }[] {
  return [
    { key: "close", label: `${base} 종가`, fmt: price },
    { key: "ma20", label: "MA20 (20일 평균)", fmt: price },
    { key: "ma60", label: "MA60 (60일 평균)", fmt: price },
    { key: "ma200", label: "MA200 (200일 평균)", fmt: price },
    { key: "ema20", label: "EMA20", fmt: price },
    { key: "atr20", label: "ATR20 (변동폭)", fmt: price },
    { key: "grid", label: "그리드 간격", fmt: (v) => fmtPct(v, 2) },
    { key: "sigma20", label: "σ20 (연율 총변동성)", fmt: (v) => fmtPct(v, 1) },
    { key: "sigma_down", label: "σ_down (하방 변동성)", fmt: (v) => fmtPct(v, 1) },
    { key: "sigma_ref", label: "σ_ref (250일 중위)", fmt: (v) => fmtPct(v, 1) },
    { key: "gap_to_ma200", label: "MA200 대비 이격", fmt: (v) => fmtPct(v, 2) },
    { key: "exit_level", label: "청산 기준선 (MA200 −2%)", fmt: price },
  ];
}

export default function SignalsPageWrapper() {
  return <Suspense fallback={null}><MarketKeyed /></Suspense>;
}

function MarketKeyed() {
  // 마켓 전환 시 페이지 상태 전체 리셋 — 이전 마켓의 결과·선택이 남는 것 방지 (2026-08-31 검토)
  const sp = useSearchParams();
  return <SignalsPage key={marketOf(sp)} />;
}

function SignalsPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const market = marketOf(sp);
  const fm = (v: number) => fmtMoneyM(market, v);
  const fpx = (v: number) => fmtPriceM(market, v);
  const NAME_X = market === "US" ? NAME_US : NAME_KR;
  const IND_ROWS = indRows(NAME_X.K200, fpx);
  const [sig, setSig] = useState<Signal | null>(null);
  const [myCapital, setMyCapital] = useState("");
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [basisPid, setBasisPid] = useState<number | null>(null);
  const [journal, setJournal] = useState<JournalDay[] | null>(null);
  const [pfHistory, setPfHistory] = useState<PortfolioTx[] | null>(null);

  async function loadSignal(pid: number | null) {
    const res = await apiFetch(`/signals/daily${pid ? `?portfolio_id=${pid}` : `?market=${market}`}`);
    if (res.ok) setSig((await res.json()) as Signal);
  }

  useEffect(() => {
    setJournal(null);
    setPfHistory(null);  // 기준 변경 → 이력도 그 기준으로 다시 (2026-08-28 검토 반영)
    void ensureSession().then(async (ok) => {
      if (!ok) { router.push("/login"); return; }
      await loadSignal(basisPid);
      const pl = await apiFetch("/portfolios");
      if (pl.ok) setPortfolios(((await pl.json()) as { items: PortfolioItem[] }).items
        .filter((it) => ((it as unknown as { market?: string }).market ?? "KR") === market));
    });
    try { setMyCapital(localStorage.getItem("myCapital") ?? ""); } catch { /* ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, basisPid, market]);

  function saveCapital(v: string) {
    setMyCapital(v);
    try { localStorage.setItem("myCapital", v); } catch { /* ignore */ }
  }

  if (!sig) return <main><PageTitle title={`주문표 · ${MARKET_LABEL[market]}`} /><p className="text-muted">불러오는 중…</p></main>;

  const r = REGIME[(sig.regime ?? "NEUTRAL") as keyof typeof REGIME] ?? REGIME.NEUTRAL;
  const cashRatio = sig.e_target !== undefined ? Math.max(1 - (sig.w_200 ?? 0) - (sig.w_lev ?? 0), 0) : 0;
  const d = sig.detail ?? {};
  const modelEquity = d.model_equity ?? d.model_capital ?? 100_000_000;
  const capInput = Number(myCapital.replaceAll(",", ""));
  const cap = market === "US" ? capInput * 100 : capInput;  // 미국은 달러 입력 → 센트
  const scale = cap > 0 ? cap / modelEquity : null;

  return (
    <main>
      <PageTitle title={`주문표 · ${MARKET_LABEL[market]}`} sub="RAVG v2.5 전략이 계산한 다음 거래일 주문 — 그리드 매수·익절은 지정가, 축소·레버리지는 시장가. 발주는 본인 HTS에서 직접 수행합니다. 모의 계산이며 투자 권유가 아닙니다." />

      {sig.status !== "OK" ? (
        <EmptyState icon="🛰️" title={sig.status === "INSUFFICIENT_HISTORY" ? "데이터 워밍업 중" : "시그널이 아직 없습니다"}
          desc={sig.reason ?? (sig.status === "INSUFFICIENT_HISTORY"
            ? "지표 계산에 약 270 거래일의 데이터가 필요합니다."
            : String(d.error ?? "장 마감 후 배치(16:05)가 실행되면 표시됩니다."))} />
      ) : (
        <div className="grid gap-4">
          {/* 기준 선택 + 안내 (2026-08-28 검토 반영) */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[14px] font-semibold text-muted">주문 기준</span>
            <select className="input !py-2" value={basisPid ?? ""}
              onChange={(e) => setBasisPid(e.target.value ? Number(e.target.value) : null)}>
              <option value="">모델 포트폴리오 (전략 참고 신호)</option>
              {portfolios.map((p) => <option key={p.id} value={p.id}>내 실전매매: {p.name}</option>)}
            </select>
          </div>
          {sig.basis === "portfolio" && sig.account ? (
            <Callout icon="🎯">
              <b className="text-ink">내 실전 포트 기준</b> 주문표입니다 — 내 보유
              (200 ETF <b className="text-ink">{fmtNum(sig.account.qty_200)}주</b> · 레버리지 <b className="text-ink">{fmtNum(sig.account.qty_lev)}주</b>)와
              현금 <b className="text-ink">{fmtNum(sig.account.cash)}원</b>을 전략 규칙에 그대로 넣어 계산했습니다.
              {sig.account.qty_200 === 0 && sig.account.qty_lev === 0 && " 현재 보유가 없어 신규 매수 그리드만 발주됩니다."}
            </Callout>
          ) : (
            <Callout icon="ℹ️">
              <b className="text-ink">모델 포트폴리오</b>(데이터 시작일부터 전략을 따라온 가상 계좌, {market === "US" ? "초기 $1M" : "초기 1억"} → 현재 평가
              <b className="text-ink"> {fm(modelEquity)}</b>) 기준의 참고 신호입니다.
              {(d.model_qty_200 ?? 0) === 0 && (d.model_qty_lev ?? 0) === 0 && " 모델 포트는 현재 전량 현금(직전 익절 완료) 상태라 신규 매수 그리드만 발주됩니다."}
              {" "}위에서 <b className="text-ink">내 실전매매</b>를 선택하면 내 보유·현금 기준 주문으로 바뀝니다.
            </Callout>
          )}

          {/* 히어로: 레짐 + 배분 + 모델 포트 상태 */}
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardTitle>시장 레짐 <span className="normal-case text-faint">· {sig.trade_date} 종가 기준</span></CardTitle>
              <div className="flex items-center gap-3">
                <span className="text-2xl font-extrabold" style={{ color: r.color }}>{r.ko}</span>
                <Badge tone={r.tone}>v{sig.version}</Badge>
              </div>
              <p className="mt-2 text-[14.5px] leading-relaxed text-muted">
                {sig.strategy === "TF"
                  ? (sig.regime === "BULL" ? "추세 위 — QQQ 전량 보유 유지" : "추세 아래 — 전량 현금 대기")
                  : r.desc}
              </p>
              <details className="mt-3">
                <summary className="cursor-pointer text-[13.5px] font-semibold text-accent">{sig.strategy === "TF" ? "판정 기준 보기" : "상승/중립/하락 기준 보기"}</summary>
                <div className="mt-2 text-[13.5px] leading-relaxed">
                  {sig.strategy === "TF" ? (
                    <span>
                      <b className="text-up">보유(상승)</b>: 종가 &gt; MA200 → 다음날 시가 전량 매수, 이후 계속 보유<br />
                      <b className="text-accent">현금(중립)</b>: 종가 &lt; MA200×0.98 (2% 관통) → 다음날 시가 전량 매도<br />
                      <span className="text-faint">그리드·레버리지 없음 — 연 3회 수준의 전환만 발생하는 미국 전용 단순 전략입니다.</span>
                    </span>
                  ) : <RegimeTip />}
                </div>
              </details>
            </Card>
            <Card>
              <CardTitle>목표 배분 <span className="normal-case text-faint">· 자산을 어떻게 나눌지의 목표치</span></CardTitle>
              {(() => {
                const emax = sig.regime === "BULL" ? 1.3 : sig.regime === "BEAR" ? 0.2 : 0.65;
                const seg = [
                  { name: NAME_X.K200, v: sig.w_200 ?? 0, bg: "var(--color-accent)", fg: "#fff" },
                  { name: "레버리지", v: sig.w_lev ?? 0, bg: "var(--color-up)", fg: "#fff" },
                  { name: "현금", v: cashRatio, bg: "var(--color-raised)", fg: "var(--color-muted)" },
                ];
                return (
                  <>
                    <div className="mb-1 flex items-baseline justify-between text-[13.5px]">
                      <span className="text-muted">실효 노출 E <Tip tip={<span>위험(변동성)을 반영해 계산한 주식 투입 비율. 변동성이 클수록 자동으로 줄어듭니다. 100%를 넘는 부분은 레버리지로 채웁니다.</span>}><span className="text-faint">ⓘ</span></Tip></span>
                      <span><b className="text-[16px]" style={{ color: r.color }}>{fmtPct(sig.e_target)}</b>
                        <span className="text-faint"> / 한도({r.ko}) {fmtPct(emax)}</span></span>
                    </div>
                    <GaugeBar ratio={(sig.e_target ?? 0) / emax} color={r.color} height={10} />
                    <div className="mb-1 mt-4 text-[13.5px] text-muted">자산 구성 <span className="text-faint">— 평가액을 이 비율로 맞추는 것이 주문의 목표</span></div>
                    <div className="flex h-7 w-full overflow-hidden rounded-lg text-[12px] font-bold">
                      {seg.filter((x) => x.v > 0.001).map((x) => (
                        <div key={x.name} className="flex items-center justify-center overflow-hidden whitespace-nowrap"
                          style={{ width: `${x.v * 100}%`, background: x.bg, color: x.fg,
                                   outline: x.name === "현금" ? "1px solid var(--color-line-strong)" : undefined }}>
                          {x.v >= 0.14 ? `${x.name} ${(x.v * 100).toFixed(1)}%` : x.v >= 0.06 ? `${(x.v * 100).toFixed(0)}%` : ""}
                        </div>
                      ))}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[13.5px] text-muted">
                      {seg.map((x) => (
                        <span key={x.name}><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm align-[-1px]"
                          style={{ background: x.bg, outline: x.name === "현금" ? "1px solid var(--color-line-strong)" : undefined }} />
                          {x.name} <b className="text-ink">{(x.v * 100).toFixed(1)}%</b></span>
                      ))}
                    </div>
                  </>
                );
              })()}
            </Card>
            <Card>
              <CardTitle>{sig.basis === "portfolio" ? "내 계좌 현황" : "모델 포트 현황"} <span className="normal-case text-faint">· 주문 수량의 기준</span></CardTitle>
              <div className="grid gap-1.5 text-[14.5px]">
                <div className="flex justify-between"><span className="text-faint">평가액</span><b>{fm(sig.basis === "portfolio" ? (sig.account?.equity ?? 0) : modelEquity)}</b></div>
                <div className="flex justify-between"><span className="text-faint">현금</span><b>{fm(sig.basis === "portfolio" ? (sig.account?.cash ?? 0) : (d.model_cash ?? 0))}</b></div>
                <div className="flex justify-between"><span className="text-faint">보유 {NAME_X.K200}</span><b>{fmtNum(sig.basis === "portfolio" ? (sig.account?.qty_200 ?? 0) : (d.model_qty_200 ?? 0))}주</b></div>
                <div className="flex justify-between"><span className="text-faint">보유 레버리지</span><b>{fmtNum(sig.basis === "portfolio" ? (sig.account?.qty_lev ?? 0) : (d.model_qty_lev ?? 0))}주</b></div>
              </div>
              {sig.basis !== "portfolio" && (
                <div className="mt-3 border-t border-line pt-3">
                  <label className="text-[13.5px] font-semibold text-muted">내 투자금으로 어림 환산({market === "US" ? "$" : "원"})</label>
                  <input className="input mt-1 w-full" placeholder="예: 50000000" value={myCapital}
                    onChange={(e) => saveCapital(e.target.value)} />
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-faint">
                    위 주문 수량은 가상 모델 계좌(현재 평가 {fm(modelEquity)}) 기준입니다. 여기에 내 투자금을 입력하면
                    <b className="text-muted"> 내 투자금 ÷ 모델 평가액</b> 비율로 줄인 &quot;내 수량&quot; 열이 주문표에 추가됩니다.
                    보유·현금이 반영되지 않은 어림값이므로, 실제 주문은 위 &quot;주문 기준&quot;에서 내 실전매매 포트를 선택해 계산하세요.
                  </p>
                </div>
              )}
            </Card>
          </div>

          {/* 주문 테이블 */}
          <Card>
            <CardTitle>다음 거래일 주문 <span className="normal-case text-faint">· {sig.basis === "portfolio" ? "내 계좌 기준 수량" : (market === "US" ? "모델 $1M 기준 수량" : "모델 1억 기준 수량")}</span></CardTitle>
            {sig.orders && sig.orders.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-[15px]">
                  <thead>
                    <tr className="border-b border-line text-left text-[13px] text-faint">
                      <th className="pb-2 font-medium">구분</th>
                      <th className="pb-2 font-medium">종목</th>
                      <th className="pb-2 font-medium">방향</th>
                      <th className="pb-2 text-right font-medium">방식 · 가격</th>
                      <th className="pb-2 text-right font-medium">수량</th>
                      {sig.basis !== "portfolio" && scale && <th className="pb-2 text-right font-medium text-accent">내 수량<div className="font-normal">(투자금 {fm(cap)} 어림)</div></th>}
                      <th className="pb-2 pl-4 font-medium">실행 조건</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sig.orders.map((o, i) => (
                      <tr key={i} className="border-b border-line/50 transition-colors last:border-0 hover:bg-inset">
                        <td className="py-3"><Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{KIND_KO[o.kind] ?? o.kind}</Badge></td>
                        <td className="py-3 font-medium">{NAME_X[o.instrument]}</td>
                        <td className={`py-3 font-bold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                        <td className="table-num py-3 font-semibold">
                          {o.price
                            ? <><span className="mr-1 rounded bg-raised px-1.5 py-0.5 text-[11.5px] font-bold text-muted">지정가</span>{fpx(o.price)}</>
                            : <><span className="mr-1 rounded bg-accent/15 px-1.5 py-0.5 text-[11.5px] font-bold text-accent">시장가</span><span className="text-[12.5px] text-faint">다음날 시가</span></>}
                        </td>
                        <td className="table-num py-3">{fmtNum(o.qty)}주</td>
                        {sig.basis !== "portfolio" && scale && <td className="table-num py-3 font-bold text-accent">{fmtNum(Math.floor(o.qty * scale))}주</td>}
                        <td className="py-3 pl-4 text-[13.5px] text-muted">{orderDesc(o, sig)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {sig.basis !== "portfolio" && !scale && <p className="mt-3 text-[13px] text-faint">💡 &quot;내 투자금&quot;을 입력하면 비례 환산 열이, 위 기준 선택에서 실전매매를 고르면 보유 반영 주문이 나옵니다.</p>}
              </div>
            ) : <p className="py-4 text-center text-muted">오늘은 신규 주문이 없습니다.</p>}
          </Card>

          {/* 조건부 지시문 — 선택된 기준의 보유·주문에 해당하는 것만 (2026-08-28 검토 반영) */}
          <Card>
            <CardTitle>조건부 지시문 <span className="normal-case text-faint">· 장중 아래 상황이 오면 직접 실행하세요</span></CardTitle>
            {sig.strategy === "TF" ? (
              <div className="grid gap-2.5">
                <Callout icon="📏">
                  {sig.regime === "BULL"
                    ? <>보유 유지 중 — 종가가 <b className="text-ink">{fpx((sig.indicators as Record<string, number>)?.exit_level ?? 0)}</b> (MA200 −2%) 아래로 <b className="text-ink">마감</b>하면 다음날 시가에 전량 매도합니다. 장중 이탈은 무시.</>
                    : <>현금 대기 중 — 종가가 <b className="text-ink">{fpx((sig.indicators as Record<string, number>)?.ma200 ?? 0)}</b> (MA200) 위로 <b className="text-ink">마감</b>하면 다음날 시가에 전량 매수합니다.</>}
                </Callout>
                <Callout icon="🕐">전환 주문은 <b className="text-ink">개장 동시호가 시장가</b>로 — 연 3회 수준이라 그 외의 날은 할 일이 없습니다.</Callout>
              </div>
            ) : (() => {
              const orders = sig.orders ?? [];
              const hasGridBuy = orders.some((o) => o.kind.startsWith("grid"));
              const levLiqOrdered = orders.some((o) => o.kind === "lev_liq");
              const levQty = sig.basis === "portfolio" ? (sig.account?.qty_lev ?? 0) : (d.model_qty_lev ?? 0);
              const items: React.ReactNode[] = [];
              if (hasGridBuy && sig.gap_cancel_below) items.push(
                <Callout key="gap" icon="⚠️">
                  시가가 <b className="text-ink">{fpx(sig.gap_cancel_below)} 이하</b>(전일종가 −1.5×ATR)로 하락 출발하면
                  잔여 그리드 매수를 <b className="text-ink">전량 취소</b>합니다. (갭 하락일에 3단이 동시에 잡히는 것을 방지)
                </Callout>);
              if (levLiqOrdered) items.push(
                <Callout key="liq" icon="🛡️">
                  레버리지 <b className="text-ink">전량 청산이 이미 오늘 주문표에 포함</b>되어 있습니다
                  (σ20 = <b className="text-ink">{fmtPct(sig.indicators?.sigma20)}</b>, 청산 기준 35%) — 장중 추가 감시는 필요 없습니다.
                </Callout>);
              else if (levQty > 0) {
                items.push(
                  <Callout key="sig20" icon="🛡️">
                    σ20 = <b className="text-ink">{fmtPct(sig.indicators?.sigma20)}</b> — 종가 기준 <b className="text-ink">35%</b> 돌파 시
                    레버리지를 전량 청산합니다.
                  </Callout>);
                items.push(
                  <Callout key="regime" icon="📉">
                    레짐이 상승에서 이탈하면 목표가를 기다리지 않고 레버리지를 <b className="text-ink">즉시 전량 청산</b>합니다.
                  </Callout>);
              }
              return items.length > 0
                ? <div className="grid gap-2.5">{items}</div>
                : <p className="text-[14px] text-faint">오늘 해당되는 조건부 지시문이 없습니다 — 그리드 매수 주문과 레버리지 보유가 없으면 장중에 감시할 항목이 없습니다.</p>;
            })()}
          </Card>

          {/* 최근 이력 — 주문 기준과 연동: 내 포트면 그 포트의 실제 매매 기록, 모델이면 모델 시뮬 이력 (2026-08-28 검토 반영) */}
          {sig.basis === "portfolio" ? (
          <Card>
            <CardTitle>내 포트 매매 이력 <span className="normal-case text-faint">· 이 포트에 실제 기록된 체결만</span></CardTitle>
            {!pfHistory ? (
              <button className="btn" onClick={() => void (async () => {
                const res = await apiFetch(`/portfolio/transactions?portfolio_id=${basisPid}`);
                if (!res.ok) return;
                const items = ((await res.json()) as { items: PortfolioTx[] }).items;
                setPfHistory(items.filter((t) => (t.kind === "buy" || t.kind === "sell") && !SEED_MEMOS.has(t.memo ?? "")));
              })()}>이 포트의 매매 이력 불러오기</button>
            ) : pfHistory.length === 0 ? (
              <p className="text-[14px] text-faint">아직 매매 이력이 없습니다 — 오늘 시작한 포트는 첫 체결을 등록하면 여기에 쌓입니다. (시작 시 입력한 보유분 등록은 매매가 아니라 표시하지 않습니다)</p>
            ) : (
              <div className="grid gap-1.5">
                {Object.entries(pfHistory.reduce<Record<string, PortfolioTx[]>>((acc, t) => {
                  const d = t.executed_at.slice(0, 10);
                  (acc[d] = acc[d] ?? []).push(t);
                  return acc;
                }, {})).map(([d, txs]) => (
                  <details key={d} className="rounded-xl border border-line bg-inset">
                    <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-4 py-2.5 text-[14px] transition-colors hover:bg-raised/60">
                      <b className="w-24">{d}</b>
                      <span className="text-[13px] text-muted">거래 {txs.length}건</span>
                      {(() => { const pnl = txs.reduce((a, t) => a + (t.realized_pnl ?? 0), 0);
                        return pnl !== 0 ? (
                          <span className={`ml-auto text-[13px] font-bold ${pnl > 0 ? "text-up" : "text-down"}`}>실현 {pnl.toLocaleString()}원</span>
                        ) : null; })()}
                    </summary>
                    <div className="grid gap-1 border-t border-line px-4 py-3 text-[13.5px]">
                      {txs.map((t) => (
                        <span key={t.id}>
                          <b className={t.kind === "buy" ? "text-up" : "text-down"}>{t.kind === "buy" ? "매수" : "매도"}</b>
                          {" "}{t.name ?? t.code} {(t.qty ?? 0).toLocaleString()}주 @ {(t.price ?? 0).toLocaleString()}원
                          {t.memo && <span className="text-faint"> — {t.memo}</span>}
                        </span>
                      ))}
                    </div>
                  </details>
                ))}
              </div>
            )}
            <p className="mt-3 text-[13px] text-faint">전략 시뮬 관점의 최근 흐름이 궁금하면 주문 기준을 &quot;모델&quot;로 바꾸면 모델 이력이 표시됩니다. 전체 기록·입출금은 실전매매 &gt; 거래 내역 참조.</p>
          </Card>
          ) : (
          <Card>
            <CardTitle>최근 신호 이력 <span className="normal-case text-faint">· 모델 포트({market === "US" ? "$1M" : "1억"}) 시뮬 기준 — 내 계좌 기록 아님</span></CardTitle>
            {!journal ? (
              <button className="btn" onClick={() => void (async () => {
                const res = await apiFetch("/signals/journal?days=20");
                if (res.ok) setJournal(((await res.json()) as { items: JournalDay[] }).items);
              })()}>최근 20 거래일 이력 불러오기</button>
            ) : (
              <div className="grid gap-1.5">
                {journal.map((dj) => (
                  <details key={dj.date} className="rounded-xl border border-line bg-inset">
                    <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-4 py-2.5 text-[14px] transition-colors hover:bg-raised/60">
                      <b className="w-24">{dj.date}</b>
                      <Badge tone={dj.regime === "BULL" ? "up" : dj.regime === "BEAR" ? "down" : "accent"}>
                        {dj.regime === "BULL" ? "상승" : dj.regime === "BEAR" ? "하락" : "중립"}
                      </Badge>
                      <span className={`w-20 text-right font-bold ${dj.day_return > 0 ? "text-up" : dj.day_return < 0 ? "text-down" : "text-muted"}`}>
                        {(dj.day_return * 100).toFixed(2)}%
                      </span>
                      <span className="hidden text-[13px] text-muted md:inline">보유 {dj.qty_200.toLocaleString()}주 · 현금 {Math.round(dj.cash / 10000).toLocaleString()}만</span>
                      <span className="ml-auto text-[13px] text-faint">주문 {dj.planned.length} · 체결 {dj.fills.length}</span>
                    </summary>
                    <div className="grid gap-1 border-t border-line px-4 py-3 text-[13.5px]">
                      {dj.fills.length === 0 && <span className="text-faint">체결 없음</span>}
                      {dj.fills.map((f, i) => (
                        <span key={i}>
                          <b className={f.side === "buy" ? "text-up" : "text-down"}>{f.side === "buy" ? "매수" : "매도"}</b>
                          {" "}{f.instrument === "K200" ? "200 ETF" : "레버리지"} {f.qty.toLocaleString()}주 @ {(f.price ?? 0).toLocaleString()}원
                          <span className="text-faint"> ({f.kind})</span>
                        </span>
                      ))}
                    </div>
                  </details>
                ))}
              </div>
            )}
          </Card>
          )}

          {/* 계산 근거 */}
          <details className="card group">
            <summary className="cursor-pointer select-none px-5 py-4 text-[13.5px] font-semibold uppercase tracking-wide text-muted transition-colors hover:text-ink">
              계산 근거 (지표값) <span className="ml-1 text-faint group-open:hidden">펼치기</span>
            </summary>
            <div className="grid gap-x-8 gap-y-1 border-t border-line px-5 py-4 sm:grid-cols-2 lg:grid-cols-3">
              {IND_ROWS.filter((row) => sig.indicators?.[row.key] !== undefined).map((row) => (
                <div key={row.key} className="flex items-baseline justify-between border-b border-line/40 py-2 text-[14.5px]">
                  <span className="text-faint">{row.label}</span>
                  <span className="font-semibold">{row.fmt(sig.indicators![row.key])}</span>
                </div>
              ))}
            </div>
          </details>
        </div>
      )}
    </main>
  );
}
