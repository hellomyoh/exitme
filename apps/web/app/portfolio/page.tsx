"use client";

/** 실전매매 기록 — 수익률 카드 + 거래 등록 (feature-portfolio §9). 비용 포함/제외 토글, 목표/손절 진행 바. */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, hasToken } from "../../lib/api";

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

const box = { background: "#1a1a22", color: "#e6e6ea", border: "1px solid #33333f", borderRadius: 6, padding: "10px 12px" } as const;
const input = { ...box, padding: "6px 8px" } as const;
const pct = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
const won = (v: number) => `${v.toLocaleString()}원`;
const pnlColor = (v: number) => (v > 0 ? "#e5484d" : v < 0 ? "#3b82f6" : "#c9c9d1");

function progress(pos: Position): { label: string; ratio: number } | null {
  if (!pos.target_price || !pos.stop_price) return null;
  const span = pos.target_price - pos.stop_price;
  if (span <= 0) return null;
  const ratio = Math.min(Math.max((pos.price - pos.stop_price) / span, 0), 1);
  return { label: `손절 ${pos.stop_price.toLocaleString()} ─ 목표 ${pos.target_price.toLocaleString()}`, ratio };
}

export default function PortfolioPage() {
  const router = useRouter();
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [pid, setPid] = useState<number | null>(null);
  const [sum, setSum] = useState<Summary | null>(null);
  const [includeCosts, setIncludeCosts] = useState(true);
  const [form, setForm] = useState({ kind: "buy", code: "069500", qty: "", price: "", amount: "", memo: "" });
  const [msg, setMsg] = useState("");

  const load = useCallback(async (id: number | null) => {
    const res = await apiFetch(`/portfolio/summary${id ? `?portfolio_id=${id}` : ""}`);
    if (res.ok) setSum((await res.json()) as Summary);
    const pl = await apiFetch("/portfolios");
    if (pl.ok) setPortfolios(((await pl.json()) as { items: PortfolioItem[] }).items);
  }, []);

  useEffect(() => {
    if (!hasToken()) { router.push("/login"); return; }
    void load(pid);
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
      setMsg(out.realized_pnl !== null ? `실현손익 ${won(out.realized_pnl)}` : "등록됨");
      void load(pid);
    } else {
      setMsg(((await res.json()) as { detail?: string }).detail ?? `등록 실패 (${res.status})`);
    }
  }

  const gross = sum ? sum.unrealized_pnl + sum.realized_pnl : 0;
  const net = sum ? gross - (includeCosts ? sum.estimated_costs : 0) : 0;

  return (
    <main style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, maxWidth: 860 }}>
      <h1 style={{ fontSize: "1.3rem" }}>실전매매 기록 <span style={{ opacity: 0.5, fontSize: 13 }}>지연 시세 기준 · 투자 권유 아님</span></h1>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <select style={input} value={pid ?? ""} onChange={(e) => setPid(e.target.value ? Number(e.target.value) : null)}>
          <option value="">내 계좌 (기본)</option>
          {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label style={{ opacity: 0.8 }}>
          <input type="checkbox" checked={includeCosts} onChange={(e) => setIncludeCosts(e.target.checked)} /> 비용 포함(추정 수수료)
        </label>
        {sum?.as_of && <span style={{ opacity: 0.5, fontSize: 12 }}>기준 {sum.as_of}</span>}
      </div>

      {sum && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 8, fontVariantNumeric: "tabular-nums" }}>
          {[["총자산", won(sum.total_equity)], ["현금", won(sum.cash)], ["주식", won(sum.stock_value)],
            ["실현손익", won(sum.realized_pnl)], ["평가손익", won(sum.unrealized_pnl)],
            ["순손익" + (includeCosts ? "(비용차감)" : ""), won(net)],
            ["TWR", pct(sum.twr)], ["XIRR", pct(sum.xirr)]].map(([k, v]) => (
            <div key={k} style={box}><div style={{ opacity: 0.6, fontSize: 12 }}>{k}</div><div style={{ fontSize: 17 }}>{v}</div></div>
          ))}
        </div>
      )}

      <section style={{ ...box, display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap" }}>
        <label>구분<br />
          <select style={input} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="buy">매수</option><option value="sell">매도</option>
            <option value="deposit">입금</option><option value="withdraw">출금</option>
          </select>
        </label>
        {(form.kind === "buy" || form.kind === "sell") ? (
          <>
            <label>종목<br />
              <select style={input} value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })}>
                <option value="069500">KODEX 200</option><option value="122630">KODEX 레버리지</option>
              </select>
            </label>
            <label>수량<br /><input style={{ ...input, width: 90 }} value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} /></label>
            <label>단가<br /><input style={{ ...input, width: 110 }} value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
          </>
        ) : (
          <label>금액<br /><input style={{ ...input, width: 140 }} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
        )}
        <label>메모<br /><input style={{ ...input, width: 160 }} value={form.memo} onChange={(e) => setForm({ ...form, memo: e.target.value })} /></label>
        <button style={{ ...box, cursor: "pointer" }} onClick={() => void submit()}>등록</button>
        {msg && <span style={{ opacity: 0.8 }}>{msg}</span>}
      </section>

      <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {sum?.positions.length === 0 && <p style={{ opacity: 0.6 }}>보유 포지션이 없습니다. 체결 내역을 등록하세요.</p>}
        {sum?.positions.map((p) => {
          const bar = progress(p);
          return (
            <div key={p.code} style={{ ...box, fontVariantNumeric: "tabular-nums" }}>
              <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                <b>{p.name} <span style={{ opacity: 0.5 }}>{p.code}</span></b>
                <span style={{ color: pnlColor(p.return), fontSize: 18 }}>{pct(p.return)} ({won(p.unrealized)})</span>
              </div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", opacity: 0.85, fontSize: 13, marginTop: 4 }}>
                <span>{p.qty.toLocaleString()}주 · 평단 {won(p.avg_price)} · 현재 {won(p.price)}</span>
                <span>보유 {p.held_days}일</span>
                <span>연환산 {p.annualized === null ? "— (30일 미만)" : pct(p.annualized)}</span>
                <span>최고 {pct(p.best_return)} / 최저 {pct(p.worst_return)}</span>
              </div>
              {bar && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 11, opacity: 0.6 }}>{bar.label}</div>
                  <div style={{ background: "#22222c", borderRadius: 4, height: 6 }}>
                    <div style={{ width: `${bar.ratio * 100}%`, background: pnlColor(p.return), height: 6, borderRadius: 4 }} />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </section>
    </main>
  );
}
