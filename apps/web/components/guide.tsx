"use client";

/** 가이드 화면 공용 조각 (2026-09-06 지시 "가이드 메뉴 신설 — 매매 공식별 설명, 수식 제외, 읽기 쉽게").
 *  글은 짧은 문단·표·단계 목록으로만 구성한다. 수식·계수는 쓰지 않고 '무엇을 왜 하는지'만 설명한다. */
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { PageTitle } from "./ui";

export const GUIDE_TABS = [
  { href: "/guide", label: "개요" },
  { href: "/guide/ravg", label: "RAVG · 한국" },
  { href: "/guide/tf", label: "TF · 미국 1배" },
  { href: "/guide/ltm", label: "LTM · 미국 레버리지" },
];

export function GuideShell({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="mx-auto max-w-4xl">
      <PageTitle title={title} sub={sub} />
      <nav className="mb-6 flex flex-wrap gap-1.5" aria-label="가이드 문서">
        {GUIDE_TABS.map((t) => {
          const active = pathname === t.href;
          return (
            <Link key={t.href} href={t.href}
              className={`rounded-full border px-3.5 py-1.5 text-[13.5px] font-semibold transition-colors ${
                active ? "border-ink bg-ink text-white" : "border-line bg-surface text-muted hover:border-line-strong hover:text-ink"}`}>
              {t.label}
            </Link>
          );
        })}
      </nav>
      <div className="grid gap-6">{children}</div>
      <p className="mt-10 text-[12.5px] leading-relaxed text-faint">
        이 가이드는 주문표가 왜 그런 주문을 내는지 이해하기 위한 설명입니다. 정확한 계산식과 상수는 알고리즘 설정(관리자)과 명세 문서에 있습니다.
        모든 수치는 과거 데이터 기반 모의 결과이며 투자 권유가 아닙니다.
      </p>
    </div>
  );
}

/** 문서 섹션 — 제목 + 본문. 본문은 문단(<P>)·목록·표 조각으로 채운다. */
export function Section({ title, children, id }: { title: string; children: ReactNode; id?: string }) {
  return (
    <section id={id} className="card p-6">
      <h2 className="mb-3 text-[17px] font-bold tracking-tight text-ink">{title}</h2>
      <div className="grid gap-3 text-[15px] leading-[1.75] text-muted">{children}</div>
    </section>
  );
}

export function P({ children }: { children: ReactNode }) {
  return <p className="break-keep">{children}</p>;
}

export function B({ children }: { children: ReactNode }) {
  return <b className="font-semibold text-ink">{children}</b>;
}

/** 한 문장 요약 — 섹션 맨 위 강조 상자 */
export function Lead({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-accent/30 bg-accent-dim px-5 py-4 text-[16px] font-semibold leading-relaxed text-ink break-keep">
      {children}
    </div>
  );
}

/** 번호 단계 — 하루 흐름·규칙 순서 */
export function Steps({ items }: { items: { title: string; desc: ReactNode }[] }) {
  return (
    <ol className="grid gap-2.5">
      {items.map((s, i) => (
        <li key={i} className="flex gap-3">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-ink text-[12px] font-bold text-white">{i + 1}</span>
          <span className="break-keep"><B>{s.title}</B> <span className="text-muted">— {s.desc}</span></span>
        </li>
      ))}
    </ol>
  );
}

/** 불릿 목록 — 항목마다 굵은 머리말 + 설명 */
export function Bullets({ items }: { items: { title: string; desc: ReactNode }[] }) {
  return (
    <ul className="grid gap-2">
      {items.map((s, i) => (
        <li key={i} className="flex gap-2.5 break-keep">
          <span className="mt-[11px] h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
          <span><B>{s.title}</B> <span className="text-muted">{s.desc}</span></span>
        </li>
      ))}
    </ul>
  );
}

/** 작은 표 — 첫 열은 굵게. 좁은 화면에서는 가로 스크롤 */
export function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-[520px] text-[14px]">
        <thead>
          <tr className="border-b border-line text-left text-[12.5px] uppercase tracking-wide text-faint">
            {head.map((h, i) => <th key={i} className="px-2 py-2 font-semibold">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line/60 align-top last:border-0">
              {r.map((c, j) => <td key={j} className={`px-2 py-2.5 leading-relaxed break-keep ${j === 0 ? "font-semibold text-ink" : "text-muted"}`}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 장점·주의 두 칸 */
export function ProsCons({ pros, cons }: { pros: string[]; cons: string[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-line bg-inset p-4">
        <div className="mb-2 text-[13px] font-bold text-up">잘 맞는 상황</div>
        <ul className="grid gap-1.5 text-[14px] leading-relaxed text-muted">{pros.map((s, i) => <li key={i} className="break-keep">· {s}</li>)}</ul>
      </div>
      <div className="rounded-xl border border-line bg-inset p-4">
        <div className="mb-2 text-[13px] font-bold text-down">알아둘 약점</div>
        <ul className="grid gap-1.5 text-[14px] leading-relaxed text-muted">{cons.map((s, i) => <li key={i} className="break-keep">· {s}</li>)}</ul>
      </div>
    </div>
  );
}

/** 주문표 라벨 사전 — 라벨(뱃지) + 뜻 */
export function Labels({ items }: { items: { label: string; tone?: "up" | "down" | "accent" | "default"; desc: ReactNode }[] }) {
  const cls = { up: "bg-up-dim text-up", down: "bg-down-dim text-down", accent: "bg-accent-dim text-accent", default: "bg-raised text-muted" };
  return (
    <ul className="grid gap-2">
      {items.map((it, i) => (
        <li key={i} className="flex flex-wrap items-baseline gap-2.5 break-keep">
          <span className={`inline-flex shrink-0 rounded-md px-2 py-0.5 text-[12.5px] font-semibold ${cls[it.tone ?? "default"]}`}>{it.label}</span>
          <span className="text-muted">{it.desc}</span>
        </li>
      ))}
    </ul>
  );
}
