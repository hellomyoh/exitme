"use client";

/**
 * 주문표 — 기준(모델 포트) 설명 + 내 투자금 환산 + 조건 설명 열 + 레짐 도움말 (2026-08-28 재설계).
 * 주문표의 기준: 시딩 시작일부터 전략을 그대로 따라온 "모델 포트폴리오(초기 1억)"의 현재 상태.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ensureSession } from "../../lib/api";
import { Badge, Callout, Card, CardTitle, EmptyState, fmtNum, fmtPct, GaugeBar, PageTitle, RegimeTip } from "../../components/ui";

type OrderRow = { instrument: string; side: string; otype: string; qty: number; price: number | null; kind: string };
type Signal = {
  status: string; reason?: string; trade_date?: string; version?: number; regime?: string;
  e_target?: number; w_200?: number; w_lev?: number; gap_cancel_below?: number;
  indicators?: Record<string, number>; orders?: OrderRow[];
  detail?: { model_capital?: number; model_equity?: number; model_cash?: number;
             model_qty_200?: number; model_qty_lev?: number; error?: string };
};

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
const NAME: Record<string, string> = { K200: "KODEX 200", LEV: "KODEX 레버리지" };

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
  };
  return map[o.kind] ?? "";
}

const IND_ROWS: { key: string; label: string; fmt: (v: number) => string }[] = [
  { key: "close", label: "KODEX 200 종가", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ma20", label: "MA20 (20일 평균)", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ma60", label: "MA60 (60일 평균)", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ma200", label: "MA200 (200일 평균)", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ema20", label: "EMA20", fmt: (v) => `${fmtNum(v)}원` },
  { key: "atr20", label: "ATR20 (변동폭)", fmt: (v) => `${fmtNum(v)}원` },
  { key: "grid", label: "그리드 간격", fmt: (v) => fmtPct(v, 2) },
  { key: "sigma20", label: "σ20 (연율 총변동성)", fmt: (v) => fmtPct(v, 1) },
  { key: "sigma_down", label: "σ_down (하방 변동성)", fmt: (v) => fmtPct(v, 1) },
  { key: "sigma_ref", label: "σ_ref (250일 중위)", fmt: (v) => fmtPct(v, 1) },
];

export default function SignalsPage() {
  const router = useRouter();
  const [sig, setSig] = useState<Signal | null>(null);
  const [myCapital, setMyCapital] = useState("");

  useEffect(() => {
    void ensureSession().then(async (ok) => {
      if (!ok) { router.push("/login"); return; }
      const res = await apiFetch("/signals/daily");
      if (res.ok) setSig((await res.json()) as Signal);
    });
    try { setMyCapital(localStorage.getItem("myCapital") ?? ""); } catch { /* ignore */ }
  }, [router]);

  function saveCapital(v: string) {
    setMyCapital(v);
    try { localStorage.setItem("myCapital", v); } catch { /* ignore */ }
  }

  if (!sig) return <main><PageTitle title="주문표" /><p className="text-muted">불러오는 중…</p></main>;

  const r = REGIME[(sig.regime ?? "NEUTRAL") as keyof typeof REGIME] ?? REGIME.NEUTRAL;
  const cashRatio = sig.e_target !== undefined ? Math.max(1 - (sig.w_200 ?? 0) - (sig.w_lev ?? 0), 0) : 0;
  const d = sig.detail ?? {};
  const modelEquity = d.model_equity ?? d.model_capital ?? 100_000_000;
  const cap = Number(myCapital.replaceAll(",", ""));
  const scale = cap > 0 ? cap / modelEquity : null;

  return (
    <main>
      <PageTitle title="주문표" sub="RAVG v2 전략이 계산한 다음 거래일 지정가 주문 — 발주는 본인 HTS에서 직접 수행합니다. 모의 계산이며 투자 권유가 아닙니다." />

      {sig.status !== "OK" ? (
        <EmptyState icon="🛰️" title={sig.status === "INSUFFICIENT_HISTORY" ? "데이터 워밍업 중" : "시그널이 아직 없습니다"}
          desc={sig.reason ?? (sig.status === "INSUFFICIENT_HISTORY"
            ? "지표 계산에 약 270 거래일의 데이터가 필요합니다."
            : String(d.error ?? "장 마감 후 배치(16:05)가 실행되면 표시됩니다."))} />
      ) : (
        <div className="grid gap-4">
          {/* 기준 안내 */}
          <Callout icon="ℹ️">
            이 주문표의 수량은 <b className="text-ink">모델 포트폴리오</b> 기준입니다 — 데이터 시작일부터 전략을 그대로 따라왔다고
            가정한 가상 계좌(초기 1억 → 현재 평가 <b className="text-ink">{fmtNum(modelEquity)}원</b>).
            아래에 <b className="text-ink">내 투자금</b>을 입력하면 내 계좌 기준 수량으로 환산해 보여줍니다.
          </Callout>

          {/* 히어로: 레짐 + 배분 + 모델 포트 상태 */}
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardTitle>시장 레짐 <span className="normal-case text-faint">· {sig.trade_date} 종가 기준</span></CardTitle>
              <div className="flex items-center gap-3">
                <span className="text-2xl font-extrabold" style={{ color: r.color }}>{r.ko}</span>
                <Badge tone={r.tone}>v{sig.version}</Badge>
              </div>
              <p className="mt-2 text-[14.5px] leading-relaxed text-muted">{r.desc}</p>
              <details className="mt-3">
                <summary className="cursor-pointer text-[13.5px] font-semibold text-accent">상승/중립/하락 기준 보기</summary>
                <div className="mt-2 text-[13.5px] leading-relaxed"><RegimeTip /></div>
              </details>
            </Card>
            <Card>
              <CardTitle>목표 배분 <span className="normal-case text-faint">· E = {fmtPct(sig.e_target)}</span></CardTitle>
              <GaugeBar ratio={(sig.e_target ?? 0) / 1.3} color={r.color} height={10} />
              <div className="mt-4 flex h-3 w-full overflow-hidden rounded-full">
                <div style={{ width: `${(sig.w_200 ?? 0) * 100}%`, background: "var(--color-accent)" }} />
                <div style={{ width: `${(sig.w_lev ?? 0) * 100}%`, background: "var(--color-up)" }} />
                <div style={{ width: `${cashRatio * 100}%`, background: "var(--color-raised)" }} />
              </div>
              <div className="mt-2 grid gap-1.5 text-[14px] text-muted">
                <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-accent" />KODEX 200 {fmtPct(sig.w_200)}</span>
                <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-up" />레버리지 {fmtPct(sig.w_lev)}</span>
                <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-raised" style={{ outline: "1px solid var(--color-line-strong)" }} />현금 {fmtPct(cashRatio)}</span>
              </div>
            </Card>
            <Card>
              <CardTitle>모델 포트 현황 <span className="normal-case text-faint">· 주문 수량의 기준</span></CardTitle>
              <div className="grid gap-1.5 text-[14.5px]">
                <div className="flex justify-between"><span className="text-faint">평가액</span><b>{fmtNum(modelEquity)}원</b></div>
                <div className="flex justify-between"><span className="text-faint">현금</span><b>{fmtNum(d.model_cash ?? 0)}원</b></div>
                <div className="flex justify-between"><span className="text-faint">보유 KODEX 200</span><b>{fmtNum(d.model_qty_200 ?? 0)}주</b></div>
                <div className="flex justify-between"><span className="text-faint">보유 레버리지</span><b>{fmtNum(d.model_qty_lev ?? 0)}주</b></div>
              </div>
              <div className="mt-3 border-t border-line pt-3">
                <label className="text-[13px] text-faint">내 투자금(원) — 수량 환산용</label>
                <input className="input mt-1 w-full" placeholder="예: 50000000" value={myCapital}
                  onChange={(e) => saveCapital(e.target.value)} />
              </div>
            </Card>
          </div>

          {/* 주문 테이블 */}
          <Card>
            <CardTitle>다음 거래일 주문</CardTitle>
            {sig.orders && sig.orders.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-[15px]">
                  <thead>
                    <tr className="border-b border-line text-left text-[13px] text-faint">
                      <th className="pb-2 font-medium">구분</th>
                      <th className="pb-2 font-medium">종목</th>
                      <th className="pb-2 font-medium">방향</th>
                      <th className="pb-2 text-right font-medium">지정가</th>
                      <th className="pb-2 text-right font-medium">수량<div className="font-normal">(모델 1억)</div></th>
                      {scale && <th className="pb-2 text-right font-medium text-accent">내 수량<div className="font-normal">({fmtNum(cap)}원)</div></th>}
                      <th className="pb-2 pl-4 font-medium">실행 조건</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sig.orders.map((o, i) => (
                      <tr key={i} className="border-b border-line/50 transition-colors last:border-0 hover:bg-inset">
                        <td className="py-3"><Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{KIND_KO[o.kind] ?? o.kind}</Badge></td>
                        <td className="py-3 font-medium">{NAME[o.instrument]}</td>
                        <td className={`py-3 font-bold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                        <td className="table-num py-3 font-semibold">{o.price ? fmtNum(o.price) : "시가"}</td>
                        <td className="table-num py-3">{fmtNum(o.qty)}주</td>
                        {scale && <td className="table-num py-3 font-bold text-accent">{fmtNum(Math.floor(o.qty * scale))}주</td>}
                        <td className="py-3 pl-4 text-[13.5px] text-muted">{orderDesc(o, sig)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!scale && <p className="mt-3 text-[13px] text-faint">💡 우측 상단 &quot;내 투자금&quot;을 입력하면 내 계좌 기준 수량 열이 추가됩니다.</p>}
              </div>
            ) : <p className="py-4 text-center text-muted">오늘은 신규 주문이 없습니다.</p>}
          </Card>

          {/* 조건부 지시문 */}
          <Card>
            <CardTitle>조건부 지시문 <span className="normal-case text-faint">· 장중 아래 상황이 오면 직접 실행하세요</span></CardTitle>
            <div className="grid gap-2.5">
              {sig.gap_cancel_below && (
                <Callout icon="⚠️">
                  시가가 <b className="text-ink">{fmtNum(sig.gap_cancel_below)}원 이하</b>(전일종가 −1.5×ATR)로 하락 출발하면
                  잔여 그리드 매수를 <b className="text-ink">전량 취소</b>합니다. (갭 하락일에 3단이 동시에 잡히는 것을 방지)
                </Callout>
              )}
              <Callout icon="🛡️">
                σ20 = <b className="text-ink">{fmtPct(sig.indicators?.sigma20)}</b> — 종가 기준 <b className="text-ink">25%</b> 돌파 시
                레버리지를 전량 청산합니다.
              </Callout>
              <Callout icon="📉">
                레짐이 상승에서 이탈하면 목표가를 기다리지 않고 레버리지를 <b className="text-ink">즉시 전량 청산</b>합니다.
              </Callout>
            </div>
          </Card>

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
