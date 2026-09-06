"use client";

/** 가이드 비교 그래프 — 전략 vs 단순 보유 자산곡선 (시작=100, 월말 샘플, 로그 축). 인라인 SVG, 라이브러리 없음 (2026-09-06 지시).
 *  로그 축을 쓰는 이유: 20년 복리 곡선을 선형으로 그리면 초반 10년이 바닥에 붙어 하락장 차이가 보이지 않는다. */
import { useState } from "react";

export type Series = { name: string; points: [string, number][]; color: string; dash?: string };

const W = 720, H = 300, PAD = { l: 44, r: 92, t: 12, b: 26 };

export function GuideChart({ series, caption }: { series: Series[]; caption?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const n = series[0].points.length;
  const allV = series.flatMap((s) => s.points.map((p) => p[1]));
  const lo = Math.min(...allV) * 0.9, hi = Math.max(...allV) * 1.1;
  const ly = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)));
  const lx = (i: number) => PAD.l + (W - PAD.l - PAD.r) * (i / Math.max(n - 1, 1));
  // y 격자: 25, 50, 100, 200, 400 … 범위 안의 2배 눈금
  const grid: number[] = [];
  for (let v = 12.5; v <= hi * 2; v *= 2) if (v >= lo && v <= hi) grid.push(v);
  // x 눈금: 연도 바뀌는 첫 점 (2~3년 간격으로 솎음)
  const years = series[0].points.map((p, i) => [i, p[0].slice(0, 4)] as [number, string])
    .filter(([i, y], k, arr) => k === 0 || arr[k - 1][1] !== y).filter((_, k) => k % (n > 150 ? 3 : 2) === 0);
  const path = (s: Series) => s.points.map((p, i) => `${i === 0 ? "M" : "L"}${lx(i).toFixed(1)},${ly(p[1]).toFixed(1)}`).join(" ");
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((x - PAD.l) / (W - PAD.l - PAD.r)) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  };
  return (
    <figure className="m-0">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[520px] select-none" role="img" aria-label={caption ?? "자산곡선 비교"}
          onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {grid.map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={ly(v)} y2={ly(v)} stroke="var(--color-line)" strokeWidth={1} />
              <text x={PAD.l - 6} y={ly(v) + 4} textAnchor="end" fontSize={11} fill="var(--color-faint)">{v >= 100 ? `×${(v / 100).toFixed(v % 100 === 0 ? 0 : 1)}` : `${Math.round(v)}`}</text>
            </g>
          ))}
          {years.map(([i, y]) => (
            <text key={i} x={lx(i)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--color-faint)">{y}</text>
          ))}
          <line x1={PAD.l} x2={W - PAD.r} y1={ly(100)} y2={ly(100)} stroke="var(--color-line-strong)" strokeWidth={1} strokeDasharray="3 3" />
          {series.map((s) => (
            <path key={s.name} d={path(s)} fill="none" stroke={s.color} strokeWidth={s.dash ? 1.6 : 2.2} strokeDasharray={s.dash} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {/* 끝점 직접 라벨 — 범례 대신 선 끝에 이름·배수 */}
          {series.map((s, k) => {
            const last = s.points[n - 1][1];
            const y = ly(last);
            return (
              <g key={s.name}>
                <circle cx={lx(n - 1)} cy={y} r={3} fill={s.color} />
                <text x={W - PAD.r + 6} y={y + 4 + (k === 0 ? 0 : 0)} fontSize={11.5} fontWeight={600} fill={s.color}>
                  ×{(last / 100).toFixed(1)} {s.name.split(" ")[0]}
                </text>
              </g>
            );
          })}
          {hover !== null && (
            <g>
              <line x1={lx(hover)} x2={lx(hover)} y1={PAD.t} y2={H - PAD.b} stroke="var(--color-faint)" strokeWidth={1} strokeDasharray="2 3" />
              {series.map((s) => <circle key={s.name} cx={lx(hover)} cy={ly(s.points[hover][1])} r={3.5} fill={s.color} stroke="var(--color-surface)" strokeWidth={1.5} />)}
              <g transform={`translate(${Math.min(lx(hover) + 10, W - PAD.r - 170)}, ${PAD.t + 4})`}>
                <rect width={166} height={16 + series.length * 16} rx={6} fill="var(--color-surface)" stroke="var(--color-line)" />
                <text x={8} y={13} fontSize={11} fill="var(--color-faint)">{series[0].points[hover][0].slice(0, 7)}</text>
                {series.map((s, k) => (
                  <text key={s.name} x={8} y={29 + k * 16} fontSize={11.5} fill="var(--color-ink)">
                    <tspan fill={s.color}>●</tspan> {s.name} <tspan fontWeight={600}>×{(s.points[hover][1] / 100).toFixed(2)}</tspan>
                  </text>
                ))}
              </g>
            </g>
          )}
        </svg>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted">
        {series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-[3px] w-4 rounded" style={{ background: s.color, opacity: s.dash ? 0.7 : 1 }} />{s.name}
          </span>
        ))}
      </div>
      {caption && <figcaption className="mt-1.5 text-[12.5px] leading-relaxed text-faint">{caption}</figcaption>}
    </figure>
  );
}
