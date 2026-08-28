"use client";

/** 주문표 — 오늘의 레짐·노출·다음 거래일 지정가 주문 + 조건부 지시문 + 계산 근거 (feature-strategy-engine §9). */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, hasToken } from "../../lib/api";

type OrderRow = { instrument: string; side: string; otype: string; qty: number; price: number | null; kind: string };
type Signal = {
  status: string; reason?: string; trade_date?: string; version?: number; regime?: string;
  e_target?: number; w_200?: number; w_lev?: number; gap_cancel_below?: number;
  indicators?: Record<string, number>; detail?: Record<string, unknown>; orders?: OrderRow[];
};

const box = { background: "#1a1a22", border: "1px solid #33333f", borderRadius: 6, padding: "10px 12px" } as const;
const REGIME_COLOR: Record<string, string> = { BULL: "#e5484d", NEUTRAL: "#e8b339", BEAR: "#3b82f6" };
const REGIME_KO: Record<string, string> = { BULL: "상승", NEUTRAL: "중립", BEAR: "하락" };
const KIND_KO: Record<string, string> = {
  grid1: "그리드 1차 매수", grid2: "그리드 2차 매수", grid3: "그리드 3차 매수", tp: "익절 매도",
  reduce: "축소 매도", lev_strat: "레버리지 전략", lev_tact1: "레버리지 전술 1차", lev_tact2: "레버리지 전술 2차",
  lev_tact_exit: "레버리지 전술 이탈", lev_liq: "레버리지 청산",
};
const NAME: Record<string, string> = { K200: "KODEX 200", LEV: "KODEX 레버리지" };
const pct = (v?: number) => (v === undefined || v === null ? "—" : `${(v * 100).toFixed(1)}%`);

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

  if (!sig) return <main style={{ padding: 24 }}>불러오는 중…</main>;

  return (
    <main style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <h1 style={{ fontSize: "1.3rem" }}>주문표 <span style={{ opacity: 0.5, fontSize: 13 }}>모의 계산이며 투자 권유가 아닙니다 — 발주는 본인 HTS에서 직접 수행</span></h1>

      {sig.status !== "OK" && (
        <div style={{ ...box, borderColor: "#e8b339" }}>
          <b>{sig.status}</b> — {sig.reason ?? (sig.status === "INSUFFICIENT_HISTORY" ? "지표 계산에 필요한 데이터(약 270 거래일)가 부족합니다" : String(sig.detail?.error ?? ""))}
        </div>
      )}

      {sig.status === "OK" && (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontVariantNumeric: "tabular-nums" }}>
            <div style={{ ...box }}>
              <div style={{ opacity: 0.6, fontSize: 12 }}>레짐 ({sig.trade_date} 종가 · v{sig.version})</div>
              <div style={{ fontSize: 20, color: REGIME_COLOR[sig.regime ?? ""] }}>{REGIME_KO[sig.regime ?? ""] ?? sig.regime}</div>
            </div>
            <div style={{ ...box }}><div style={{ opacity: 0.6, fontSize: 12 }}>목표 노출 E</div><div style={{ fontSize: 20 }}>{pct(sig.e_target)}</div></div>
            <div style={{ ...box }}><div style={{ opacity: 0.6, fontSize: 12 }}>KODEX 200</div><div style={{ fontSize: 20 }}>{pct(sig.w_200)}</div></div>
            <div style={{ ...box }}><div style={{ opacity: 0.6, fontSize: 12 }}>레버리지</div><div style={{ fontSize: 20 }}>{pct(sig.w_lev)}</div></div>
          </div>

          <section style={{ ...box }}>
            <h2 style={{ fontSize: "1rem", marginBottom: 8 }}>다음 거래일 주문 (모델 자본 1억 기준 수량)</h2>
            {sig.orders && sig.orders.length > 0 ? (
              <table style={{ width: "100%", fontVariantNumeric: "tabular-nums", borderCollapse: "collapse" }}>
                <thead><tr style={{ opacity: 0.6, textAlign: "left" }}><th>구분</th><th>종목</th><th>방향</th><th>유형</th><th style={{ textAlign: "right" }}>지정가</th><th style={{ textAlign: "right" }}>수량</th></tr></thead>
                <tbody>
                  {sig.orders.map((o, i) => (
                    <tr key={i} style={{ borderTop: "1px solid #22222c" }}>
                      <td>{KIND_KO[o.kind] ?? o.kind}</td><td>{NAME[o.instrument]}</td>
                      <td style={{ color: o.side === "buy" ? "#e5484d" : "#3b82f6" }}>{o.side === "buy" ? "매수" : "매도"}</td>
                      <td>{o.otype === "limit" ? "지정가" : "시장가"}</td>
                      <td style={{ textAlign: "right" }}>{o.price ? o.price.toLocaleString() : "시가"}</td>
                      <td style={{ textAlign: "right" }}>{o.qty.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p style={{ opacity: 0.7 }}>오늘은 신규 주문이 없습니다.</p>}
          </section>

          <section style={{ ...box }}>
            <h2 style={{ fontSize: "1rem", marginBottom: 8 }}>조건부 지시문</h2>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
              {sig.gap_cancel_below && (
                <li>시가가 <b>{sig.gap_cancel_below.toLocaleString()}원 이하</b>(전일종가 −1.5×ATR)로 하락하면 잔여 그리드 매수 <b>전량 취소</b></li>
              )}
              <li>σ20 = <b>{pct(sig.indicators?.sigma20)}</b> — 종가 기준 25% 돌파 시 레버리지 전량 청산</li>
              <li>레짐이 상승에서 이탈하면 레버리지 전량 청산 (목표가 대기 금지)</li>
            </ul>
          </section>

          <details style={{ ...box }}>
            <summary style={{ cursor: "pointer" }}>계산 근거 (지표값)</summary>
            <table style={{ marginTop: 8, fontVariantNumeric: "tabular-nums" }}>
              <tbody>
                {Object.entries(sig.indicators ?? {}).map(([k, v]) => (
                  <tr key={k}><td style={{ opacity: 0.6, paddingRight: 16 }}>{k}</td><td>{typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </main>
  );
}
