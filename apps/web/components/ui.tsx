"use client";

/** 공용 UI 프리미티브 — 디자인 토큰(globals.css) 기반. 화면 간 일관성의 단일 출처. */
import { useRef, useState, type ReactNode } from "react";
import { Spark } from "./spark";

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
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="min-w-0 text-[13.5px] font-semibold uppercase tracking-wide text-muted">{children}</h2>
      {/* 우측 액션은 제목이 길어도 세로로 짜부라지지 않게 (2026-09-02 모바일) */}
      {right && <div className="shrink-0 whitespace-nowrap">{right}</div>}
    </div>
  );
}

export function Stat({ label, value, tone = "default", hint, tip, spark, sparkColor, sub, size = "md" }: {
  label: string; value: ReactNode; tone?: "default" | "up" | "down" | "accent"; hint?: string; tip?: ReactNode;
  spark?: number[] | null; sparkColor?: string;  // 카드 하단 미니 추세 (2026-09-05, Zenith 스타일)
  sub?: ReactNode;          // 값 아래 보조 정보 한 줄 (예: 구성·세부 손익) — 카드 수를 줄이기 위한 2차 정보 (2026-09-05)
  size?: "md" | "lg";       // lg = 상단 핵심 카드 (값 24px)
}) {
  const color = { default: "text-ink", up: "text-up", down: "text-down", accent: "text-accent" }[tone];
  const labelEl = tip ? <Tip tip={tip}><span>{label}</span><span className="text-faint">ⓘ</span></Tip> : label;
  return (
    <div className={`card px-4 ${size === "lg" ? "py-4" : "py-3.5"}`}>
      <div className="text-[13px] text-faint">{labelEl}</div>
      {/* nowrap 금지 — 긴 값(금액+%)은 공백에서 줄바꿈되어 카드 밖으로 넘치지 않게 (2026-09-02) */}
      <div className={`mt-1 break-keep font-bold leading-snug ${size === "lg" ? "text-[24px]" : "text-[19px]"} ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-[12.5px] leading-relaxed text-muted">{sub}</div>}
      {hint && <div className="mt-0.5 text-[11px] text-faint">{hint}</div>}
      {spark && spark.length >= 2 && <Spark data={spark} color={sparkColor} />}
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

/** 마우스 롤오버 도움말 — label 위에 hover 시 툴팁 표시.
 *  화면 오른쪽 끝(TWR·XIRR 카드 등)에서는 왼쪽으로, 화면 위쪽에서는 아래로 펼친다 —
 *  항상 left-0/bottom-full 로만 열려 오른쪽 카드의 도움말이 화면 밖으로 잘리던 문제 (2026-09-05 지시). */
export function Tip({ tip, children }: { tip: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [side, setSide] = useState<"left" | "right">("left");
  const [vert, setVert] = useState<"up" | "down">("up");
  const place = () => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setSide(r.left + 336 > window.innerWidth - 12 ? "right" : "left");  // 툴팁 폭 320 + 여백
    setVert(r.top < 240 ? "down" : "up");
  };
  return (
    <span ref={ref} tabIndex={0} onMouseEnter={place} onFocus={place}
      className="group relative inline-flex cursor-help items-center gap-1 outline-none">
      {children}
      <span className={`pointer-events-none invisible absolute z-30 w-80 max-w-[calc(100vw-2.5rem)] rounded-xl border border-line bg-surface p-3.5 text-left text-[13.5px] font-normal normal-case leading-relaxed text-muted opacity-0 shadow-lg transition-opacity duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100 ${
        side === "right" ? "right-0" : "left-0"} ${vert === "up" ? "bottom-full mb-2" : "top-full mt-2"}`}>
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
      <b className="text-down">하락장</b>: 종가&lt;200일선 <b>그리고</b> 20일선&lt;60일선 → 신규 매수 정지·보유 축소(최대 20%)·레버리지 청산<br />
      <span className="text-faint">※ 이탈에는 완충 2%: 경계를 2% 관통해야 판정이 바뀝니다 (잦은 전환 방지). 반대 조건 동시 충족 시엔 즉시 직행.</span>
    </span>
  );
}

export const fmtWon = (v: number) => `${Math.round(v).toLocaleString()}원`;
export const fmtNum = (v: number) => Math.round(v).toLocaleString();
export const fmtPct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`;
export const pnlTone = (v: number): "up" | "down" | "default" => (v > 0 ? "up" : v < 0 ? "down" : "default");
