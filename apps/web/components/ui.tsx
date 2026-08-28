/** 공용 UI 프리미티브 — 디자인 토큰(globals.css) 기반. 화면 간 일관성의 단일 출처. */
import type { ReactNode } from "react";

export function PageTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-5">
      <h1 className="text-xl font-bold tracking-tight">{title}</h1>
      {sub && <p className="mt-1 text-[13px] text-muted">{sub}</p>}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`card p-5 ${className}`}>{children}</section>;
}

export function CardTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <h2 className="text-[13px] font-semibold uppercase tracking-wide text-muted">{children}</h2>
      {right}
    </div>
  );
}

export function Stat({ label, value, tone = "default", hint }: {
  label: string; value: ReactNode; tone?: "default" | "up" | "down" | "accent"; hint?: string;
}) {
  const color = { default: "text-ink", up: "text-up", down: "text-down", accent: "text-accent" }[tone];
  return (
    <div className="card px-4 py-3.5">
      <div className="text-xs text-faint">{label}</div>
      <div className={`mt-1 text-lg font-bold ${color}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-faint">{hint}</div>}
    </div>
  );
}

export function Badge({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "up" | "down" | "accent" | "ok" }) {
  const cls = {
    default: "bg-raised text-muted border-line",
    up: "bg-up-dim text-up border-transparent",
    down: "bg-down-dim text-down border-transparent",
    accent: "bg-accent-dim text-accent border-transparent",
    ok: "bg-[rgba(53,194,143,0.13)] text-ok border-transparent",
  }[tone];
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-semibold ${cls}`}>
      {children}
    </span>
  );
}

export function Callout({ icon, children }: { icon: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-line bg-inset px-4 py-3 text-[13.5px] leading-relaxed text-muted">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span>{children}</span>
    </div>
  );
}

export function EmptyState({ icon, title, desc, action }: { icon: string; title: string; desc?: string; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center gap-2 px-6 py-12 text-center">
      <div className="text-3xl">{icon}</div>
      <div className="font-semibold">{title}</div>
      {desc && <p className="max-w-md text-[13px] text-muted">{desc}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** 수평 게이지 바 — 노출·목표 진행 등 */
export function GaugeBar({ ratio, color = "var(--color-accent)", height = 8 }: { ratio: number; color?: string; height?: number }) {
  return (
    <div className="w-full overflow-hidden rounded-full bg-inset" style={{ height }}>
      <div className="h-full rounded-full transition-all duration-500"
        style={{ width: `${Math.min(Math.max(ratio, 0), 1) * 100}%`, background: color }} />
    </div>
  );
}

export const fmtWon = (v: number) => `${Math.round(v).toLocaleString()}원`;
export const fmtNum = (v: number) => Math.round(v).toLocaleString();
export const fmtPct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`;
export const pnlTone = (v: number): "up" | "down" | "default" => (v > 0 ? "up" : v < 0 ? "down" : "default");
