"use client";

/** 실전매매 기록 — 수익률 카드 + 거래 등록 (feature-portfolio §9). */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ensureSession } from "../../lib/api";
import { Badge, Card, CardTitle, EmptyState, fmtPct, fmtWon, GaugeBar, PageTitle, pnlTone, Stat } from "../../components/ui";

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

const toneCls = { up: "text-up", down: "text-down", default: "text-ink" };

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

  const net = sum ? sum.unrealized_pnl + sum.realized_pnl - (includeCosts ? sum.estimated_costs : 0) : 0;

  return (
    <main>
      <PageTitle title="실전매매" sub="체결 내역을 등록해 매수 시점 기준 수익률을 추적합니다 — 지연 시세 기준" />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select className="input" value={pid ?? ""} onChange={(e) => setPid(e.target.value ? Number(e.target.value) : null)}>
          <option value="">내 계좌 (기본)</option>
          {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-[13px] text-muted">
          <input type="checkbox" className="accent-[#f0b429]" checked={includeCosts} onChange={(e) => setIncludeCosts(e.target.checked)} />
          비용 포함 (추정 수수료)
        </label>
        {sum?.as_of && <span className="ml-auto text-xs text-faint">기준일 {sum.as_of} · 지연 시세</span>}
      </div>

      {sum && (
        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
          <Stat label="총자산" value={fmtWon(sum.total_equity)} />
          <Stat label="현금" value={fmtWon(sum.cash)} />
          <Stat label="주식" value={fmtWon(sum.stock_value)} />
          <Stat label="실현손익" value={fmtWon(sum.realized_pnl)} tone={pnlTone(sum.realized_pnl)} />
          <Stat label="평가손익" value={fmtWon(sum.unrealized_pnl)} tone={pnlTone(sum.unrealized_pnl)} />
          <Stat label={`순손익${includeCosts ? " (비용차감)" : ""}`} value={fmtWon(net)} tone={pnlTone(net)} />
          <Stat label="TWR" value={fmtPct(sum.twr, 2)} />
          <Stat label="XIRR" value={fmtPct(sum.xirr, 2)} />
        </div>
      )}

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
    </main>
  );
}
