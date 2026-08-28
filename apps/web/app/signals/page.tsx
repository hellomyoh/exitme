"use client";

/** 주문표 — 레짐·노출 히어로 + 주문 테이블 + 조건부 지시문 + 계산 근거 (feature-strategy-engine §9). */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, hasToken } from "../../lib/api";
import { Badge, Callout, Card, CardTitle, EmptyState, fmtNum, fmtPct, GaugeBar, PageTitle } from "../../components/ui";

type OrderRow = { instrument: string; side: string; otype: string; qty: number; price: number | null; kind: string };
type Signal = {
  status: string; reason?: string; trade_date?: string; version?: number; regime?: string;
  e_target?: number; w_200?: number; w_lev?: number; gap_cancel_below?: number;
  indicators?: Record<string, number>; detail?: Record<string, unknown>; orders?: OrderRow[];
};

const REGIME = {
  BULL: { ko: "상승장", color: "var(--color-up)", tone: "up" as const, desc: "그리드 매수 + 코어 보유 (익절 없음)" },
  NEUTRAL: { ko: "중립장", color: "var(--color-accent)", tone: "accent" as const, desc: "그리드 왕복 — 매수 후 +Grid 익절" },
  BEAR: { ko: "하락장", color: "var(--color-down)", tone: "down" as const, desc: "신규 매수 정지 · 보유 축소" },
};
const KIND_KO: Record<string, string> = {
  grid1: "그리드 1차", grid2: "그리드 2차", grid3: "그리드 3차", tp: "익절",
  reduce: "축소", lev_strat: "레버리지 전략", lev_tact1: "레버리지 전술 1차",
  lev_tact2: "레버리지 전술 2차", lev_tact_exit: "전술 이탈", lev_liq: "레버리지 청산",
};
const NAME: Record<string, string> = { K200: "KODEX 200", LEV: "KODEX 레버리지" };

const IND_ROWS: { key: string; label: string; fmt: (v: number) => string }[] = [
  { key: "close", label: "KODEX 200 종가", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ma20", label: "MA20", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ma60", label: "MA60", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ma200", label: "MA200", fmt: (v) => `${fmtNum(v)}원` },
  { key: "ema20", label: "EMA20", fmt: (v) => `${fmtNum(v)}원` },
  { key: "atr20", label: "ATR20", fmt: (v) => `${fmtNum(v)}원` },
  { key: "grid", label: "그리드 간격", fmt: (v) => fmtPct(v, 2) },
  { key: "sigma20", label: "σ20 (연율 총변동성)", fmt: (v) => fmtPct(v, 1) },
  { key: "sigma_down", label: "σ_down (하방 변동성)", fmt: (v) => fmtPct(v, 1) },
  { key: "sigma_ref", label: "σ_ref (250일 중위)", fmt: (v) => fmtPct(v, 1) },
  { key: "lev_close", label: "레버리지 종가", fmt: (v) => `${fmtNum(v)}원` },
  { key: "equity", label: "모델 포트 평가액", fmt: (v) => `${fmtNum(v)}원` },
];

export default function SignalsPage() {
  const router = useRouter();
  const [sig, setSig] = useState<Signal | null>(null);

  useEffect(() => {
    if (!hasToken()) { router.push("/login"); return; }
    void (async () => {
      const res = await apiFetch("/signals/daily");
      if (res.ok) setSig((await res.json()) as Signal);
    })();
  }, [router]);

  if (!sig) return <main><PageTitle title="주문표" /><p className="text-muted">불러오는 중…</p></main>;

  const r = REGIME[(sig.regime ?? "NEUTRAL") as keyof typeof REGIME] ?? REGIME.NEUTRAL;
  const cashRatio = sig.e_target !== undefined ? Math.max(1 - (sig.w_200 ?? 0) - (sig.w_lev ?? 0), 0) : 0;

  return (
    <main>
      <PageTitle title="주문표" sub="다음 거래일 지정가 주문 — 발주는 본인 HTS에서 직접 수행합니다. 모의 계산이며 투자 권유가 아닙니다." />

      {sig.status !== "OK" ? (
        <EmptyState icon="🛰️" title={sig.status === "INSUFFICIENT_HISTORY" ? "데이터 워밍업 중" : "시그널이 아직 없습니다"}
          desc={sig.reason ?? (sig.status === "INSUFFICIENT_HISTORY"
            ? "지표 계산에 약 270 거래일의 데이터가 필요합니다."
            : String(sig.detail?.error ?? "장 마감 후 배치(16:05)가 실행되면 표시됩니다."))} />
      ) : (
        <div className="grid gap-4">
          {/* 히어로: 레짐 + 배분 */}
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardTitle>시장 레짐 <span className="normal-case text-faint">· {sig.trade_date} 종가 기준</span></CardTitle>
              <div className="flex items-center gap-3">
                <span className="text-2xl font-extrabold" style={{ color: r.color }}>{r.ko}</span>
                <Badge tone={r.tone}>v{sig.version}</Badge>
              </div>
              <p className="mt-2 text-[13px] text-muted">{r.desc}</p>
            </Card>
            <Card className="md:col-span-2">
              <CardTitle>목표 노출 E = {fmtPct(sig.e_target)}</CardTitle>
              <GaugeBar ratio={(sig.e_target ?? 0) / 1.3} color={r.color} height={10} />
              <div className="mt-4 flex h-3 w-full overflow-hidden rounded-full">
                <div style={{ width: `${(sig.w_200 ?? 0) * 100}%`, background: "var(--color-accent)" }} />
                <div style={{ width: `${(sig.w_lev ?? 0) * 100}%`, background: "var(--color-up)" }} />
                <div style={{ width: `${cashRatio * 100}%`, background: "var(--color-raised)" }} />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-muted">
                <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-accent" />KODEX 200 {fmtPct(sig.w_200)}</span>
                <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-up" />레버리지 {fmtPct(sig.w_lev)}</span>
                <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-raised" style={{ outline: "1px solid var(--color-line-strong)" }} />현금 {fmtPct(cashRatio)}</span>
              </div>
            </Card>
          </div>

          {/* 주문 테이블 */}
          <Card>
            <CardTitle>다음 거래일 주문 <span className="normal-case text-faint">· 모델 자본 1억 기준 수량</span></CardTitle>
            {sig.orders && sig.orders.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-[13.5px]">
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-faint">
                      <th className="pb-2 font-medium">구분</th>
                      <th className="pb-2 font-medium">종목</th>
                      <th className="pb-2 font-medium">방향</th>
                      <th className="pb-2 font-medium">유형</th>
                      <th className="pb-2 text-right font-medium">지정가</th>
                      <th className="pb-2 text-right font-medium">수량</th>
                      <th className="pb-2 text-right font-medium">주문금액</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sig.orders.map((o, i) => (
                      <tr key={i} className="border-b border-line/50 transition-colors last:border-0 hover:bg-raised/50">
                        <td className="py-2.5"><Badge tone={o.kind.startsWith("lev") ? "up" : o.kind === "tp" ? "ok" : "accent"}>{KIND_KO[o.kind] ?? o.kind}</Badge></td>
                        <td className="py-2.5 font-medium">{NAME[o.instrument]}</td>
                        <td className={`py-2.5 font-bold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</td>
                        <td className="py-2.5 text-muted">{o.otype === "limit" ? "지정가" : "시장가"}</td>
                        <td className="table-num py-2.5 font-semibold">{o.price ? fmtNum(o.price) : "시가"}</td>
                        <td className="table-num py-2.5">{fmtNum(o.qty)}</td>
                        <td className="table-num py-2.5 text-muted">{o.price ? fmtNum(o.price * o.qty) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="py-4 text-center text-muted">오늘은 신규 주문이 없습니다.</p>}
          </Card>

          {/* 조건부 지시문 */}
          <Card>
            <CardTitle>조건부 지시문</CardTitle>
            <div className="grid gap-2.5">
              {sig.gap_cancel_below && (
                <Callout icon="⚠️">
                  시가가 <b className="text-ink">{fmtNum(sig.gap_cancel_below)}원 이하</b>(전일종가 −1.5×ATR)로 하락하면
                  잔여 그리드 매수를 <b className="text-ink">전량 취소</b>합니다. (갭 하락 방어)
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
            <summary className="cursor-pointer select-none px-5 py-4 text-[13px] font-semibold uppercase tracking-wide text-muted transition-colors hover:text-ink">
              계산 근거 (지표값) <span className="ml-1 text-faint group-open:hidden">펼치기</span>
            </summary>
            <div className="grid gap-x-8 gap-y-1 border-t border-line px-5 py-4 sm:grid-cols-2 lg:grid-cols-3">
              {IND_ROWS.filter((row) => sig.indicators?.[row.key] !== undefined).map((row) => (
                <div key={row.key} className="flex items-baseline justify-between border-b border-line/40 py-1.5 text-[13px]">
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
