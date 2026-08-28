/** 공용 UI 프리미티브 — 디자인 토큰(globals.css) 기반. 화면 간 일관성의 단일 출처. */
import type { ReactNode } from "react";

export function PageTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-5">
      <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
      {sub && <p className="mt-1.5 text-[14.5px] text-muted">{sub}</p>}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`card p-5 ${className}`}>{children}</section>;
}

export function CardTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <h2 className="text-[13.5px] font-semibold uppercase tracking-wide text-muted">{children}</h2>
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
      <div className="text-[13px] text-faint">{label}</div>
      <div className={`mt-1 text-[21px] font-bold ${color}`}>{value}</div>
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
    <span className={`inline-flex items-center rounded-md border px-2.5 py-1 text-[13px] font-semibold ${cls}`}>
      {children}
    </span>
  );
}

export function Callout({ icon, children }: { icon: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-line bg-inset px-4 py-3 text-[14.5px] leading-relaxed text-muted">
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

/** 마우스 롤오버 도움말 — label 위에 hover 시 툴팁 표시 */
export function Tip({ tip, children }: { tip: ReactNode; children: ReactNode }) {
  return (
    <span className="group relative inline-flex cursor-help items-center gap-1">
      {children}
      <span className="pointer-events-none invisible absolute bottom-full left-0 z-30 mb-2 w-80 rounded-xl border border-line bg-surface p-3.5 text-left text-[13.5px] font-normal normal-case leading-relaxed text-muted opacity-0 shadow-lg transition-opacity duration-150 group-hover:visible group-hover:opacity-100">
        {tip}
      </span>
    </span>
  );
}

/** 레짐 도움말 본문 — 상승/중립/하락 기준과 운용 (전략 정본 §4·§9) */
export function RegimeTip() {
  return (
    <span>
      <b className="text-up">상승장</b>: 종가&gt;200일선 <b>그리고</b> 20일선&gt;60일선 → 그리드로 사서 <b className="text-ink">익절 없이 보유</b>, 레버리지 허용<br />
      <b className="text-accent">중립장</b>: 상승·하락 어느 쪽도 아닌 구간 → 떨어지면 사고 <b className="text-ink">+Grid% 오르면 익절</b>(왕복)<br />
      <b className="text-down">하락장</b>: 종가&lt;200일선 <b>그리고</b> 20일선&lt;60일선 → 신규 매수 정지·보유 축소(최대 20%)·레버리지 청산
    </span>
  );
}

export const fmtWon = (v: number) => `${Math.round(v).toLocaleString()}원`;
export const fmtNum = (v: number) => Math.round(v).toLocaleString();
export const fmtPct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`;
export const pnlTone = (v: number): "up" | "down" | "default" => (v > 0 ? "up" : v < 0 ? "down" : "default");
