"use client";

/** 가이드 비교 그래프 — 인라인 SVG, 라이브러리 없음 (2026-09-06 지시, 같은 날 개편).
 *  GuideChart   : 자산곡선(시작=100, 월말, 로그 축) — 20년 복리를 한 화면에 넣기 위한 로그 축. 위기 구간(보유 −20% 이상) 음영.
 *  DrawdownChart: 낙폭(수면 아래) 곡선 — 고점 대비 %, 월중 최저. '위험 구간에서 얼마나 덜 빠졌나'를 직접 보여 주는 핵심 그래프.
 *  YearStrip    : 연도별 수익 띠 — 공식이 도운 해와 뒤진 해를 한 줄에. */
import React, { useState } from "react";

export type Series = { name: string; points: [string, number][]; color: string; dash?: string };
export type Shade = [string, string][];

const W = 720, PAD = { l: 44, r: 92, t: 12, b: 26 };

function useHover(n: number, H: number) {
  const [hover, setHover] = useState<number | null>(null);
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((x - PAD.l) / (W - PAD.l - PAD.r)) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  };
  void H;
  return { hover, onMove, onLeave: () => setHover(null) };
}

const lx = (i: number, n: number) => PAD.l + (W - PAD.l - PAD.r) * (i / Math.max(n - 1, 1));

function yearTicks(points: [string, number][]) {
  const n = points.length;
  return points.map((p, i) => [i, p[0].slice(0, 4)] as [number, string])
    .filter(([, y], k, arr) => k === 0 || arr[k - 1][1] !== y).filter((_, k) => k % (n > 150 ? 3 : 2) === 0);
}

/** 음영 구간 → x 범위 (날짜 문자열 비교로 인덱스 탐색) */
function shadeRects(shade: Shade | undefined, points: [string, number][], top: number, bottom: number) {
  if (!shade) return null;
  const n = points.length;
  return shade.map(([a, b], k) => {
    const i0 = points.findIndex((p) => p[0] >= a);
    let i1 = points.findIndex((p) => p[0] >= b);
    if (i0 < 0) return null;
    if (i1 < 0) i1 = n - 1;
    return <rect key={k} x={lx(i0, n)} y={top} width={Math.max(lx(i1, n) - lx(i0, n), 2)} height={bottom - top} fill="var(--color-down)" opacity={0.07} />;
  });
}

function Legend({ series }: { series: Series[] }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted">
      {series.map((s) => (
        <span key={s.name} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-[3px] w-4 rounded" style={{ background: s.color, opacity: s.dash ? 0.7 : 1 }} />{s.name}
        </span>
      ))}
    </div>
  );
}

export function GuideChart({ series, caption, shade }: { series: Series[]; caption?: string; shade?: Shade }) {
  const H = 280;
  const n = series[0].points.length;
  const { hover, onMove, onLeave } = useHover(n, H);
  const allV = series.flatMap((s) => s.points.map((p) => p[1]));
  const lo = Math.min(...allV) * 0.9, hi = Math.max(...allV) * 1.1;
  const ly = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)));
  const grid: number[] = [];
  for (let v = 12.5; v <= hi * 2; v *= 2) if (v >= lo && v <= hi) grid.push(v);
  const path = (s: Series) => s.points.map((p, i) => `${i === 0 ? "M" : "L"}${lx(i, n).toFixed(1)},${ly(p[1]).toFixed(1)}`).join(" ");
  return (
    <figure className="m-0">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[520px] select-none" role="img" aria-label={caption ?? "자산곡선 비교"}
          onMouseMove={onMove} onMouseLeave={onLeave}>
          {shadeRects(shade, series[0].points, PAD.t, H - PAD.b)}
          {grid.map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={ly(v)} y2={ly(v)} stroke="var(--color-line)" strokeWidth={1} />
              <text x={PAD.l - 6} y={ly(v) + 4} textAnchor="end" fontSize={11} fill="var(--color-faint)">{v >= 100 ? `×${(v / 100).toFixed(v % 100 === 0 ? 0 : 1)}` : `${Math.round(v)}`}</text>
            </g>
          ))}
          {yearTicks(series[0].points).map(([i, y]) => (
            <text key={i} x={lx(i, n)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--color-faint)">{y}</text>
          ))}
          <line x1={PAD.l} x2={W - PAD.r} y1={ly(100)} y2={ly(100)} stroke="var(--color-line-strong)" strokeWidth={1} strokeDasharray="3 3" />
          {series.map((s) => (
            <path key={s.name} d={path(s)} fill="none" stroke={s.color} strokeWidth={s.dash ? 1.6 : 2.2} strokeDasharray={s.dash} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {(() => {
            // 끝 라벨 — 값이 비슷해 겹치면 12px 간격으로 밀어낸다
            const labels = series.map((s) => ({ s, last: s.points[n - 1][1], y: ly(s.points[n - 1][1]) })).sort((a, b) => a.y - b.y);
            for (let k = 1; k < labels.length; k++) if (labels[k].y - labels[k - 1].y < 12) labels[k].y = labels[k - 1].y + 12;
            return labels.map(({ s, last, y }) => (
              <g key={s.name}>
                <circle cx={lx(n - 1, n)} cy={ly(last)} r={3} fill={s.color} />
                <text x={W - PAD.r + 6} y={y + 4} fontSize={11.5} fontWeight={600} fill={s.color}>×{(last / 100).toFixed(1)} {s.name.split(" ")[0]}</text>
              </g>
            ));
          })()}
          {hover !== null && (
            <g>
              <line x1={lx(hover, n)} x2={lx(hover, n)} y1={PAD.t} y2={H - PAD.b} stroke="var(--color-faint)" strokeWidth={1} strokeDasharray="2 3" />
              {series.map((s) => <circle key={s.name} cx={lx(hover, n)} cy={ly(s.points[hover][1])} r={3.5} fill={s.color} stroke="var(--color-surface)" strokeWidth={1.5} />)}
              <g transform={`translate(${Math.min(lx(hover, n) + 10, W - PAD.r - 170)}, ${PAD.t + 4})`}>
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
      <Legend series={series} />
      {caption && <figcaption className="mt-1.5 text-[12.5px] leading-relaxed text-faint">{caption}</figcaption>}
    </figure>
  );
}

/** 낙폭 그래프 — 값은 고점 대비 비율(0 ~ −1). 보유는 회색 면, 공식은 색 면으로 겹쳐 그려 '얼마나 덜 잠겼나'를 보인다. */
export function DrawdownChart({ series, caption, shade, floor = -0.5 }: { series: Series[]; caption?: string; shade?: Shade; floor?: number }) {
  const H = 220;
  const n = series[0].points.length;
  const { hover, onMove, onLeave } = useHover(n, H);
  const minV = Math.min(floor, ...series.flatMap((s) => s.points.map((p) => p[1])));
  const ly = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (-v / -minV);
  const ticks = [-0.1, -0.2, -0.3, -0.4, -0.5, -0.6, -0.7, -0.8].filter((t) => t >= minV - 1e-9);
  const area = (s: Series) => `M${lx(0, n)},${ly(0)} ` + s.points.map((p, i) => `L${lx(i, n).toFixed(1)},${ly(p[1]).toFixed(1)}`).join(" ") + ` L${lx(n - 1, n)},${ly(0)} Z`;
  const line = (s: Series) => s.points.map((p, i) => `${i === 0 ? "M" : "L"}${lx(i, n).toFixed(1)},${ly(p[1]).toFixed(1)}`).join(" ");
  return (
    <figure className="m-0">
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[520px] select-none" role="img" aria-label={caption ?? "낙폭 비교"}
          onMouseMove={onMove} onMouseLeave={onLeave}>
          {shadeRects(shade, series[0].points, PAD.t, H - PAD.b)}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={ly(t)} y2={ly(t)} stroke="var(--color-line)" strokeWidth={1} />
              <text x={PAD.l - 6} y={ly(t) + 4} textAnchor="end" fontSize={11} fill="var(--color-faint)">{Math.round(t * 100)}%</text>
            </g>
          ))}
          <line x1={PAD.l} x2={W - PAD.r} y1={ly(0)} y2={ly(0)} stroke="var(--color-line-strong)" strokeWidth={1} />
          <text x={PAD.l - 6} y={ly(0) + 4} textAnchor="end" fontSize={11} fill="var(--color-faint)">0</text>
          {yearTicks(series[0].points).map(([i, y]) => (
            <text key={i} x={lx(i, n)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--color-faint)">{y}</text>
          ))}
          {/* 면: 보유(회색·뒤) → 공식(색·앞). 선은 위에 다시 그린다 */}
          {[...series].reverse().map((s) => (
            <path key={`a-${s.name}`} d={area(s)} fill={s.color} opacity={s.dash ? 0.18 : 0.28} />
          ))}
          {series.map((s) => (
            <path key={`l-${s.name}`} d={line(s)} fill="none" stroke={s.color} strokeWidth={s.dash ? 1.3 : 1.8} strokeDasharray={s.dash} strokeLinejoin="round" />
          ))}
          {/* 최저점 라벨 */}
          {series.map((s) => {
            let mi = 0;
            s.points.forEach((p, i) => { if (p[1] < s.points[mi][1]) mi = i; });
            const v = s.points[mi][1];
            return (
              <g key={`m-${s.name}`}>
                <circle cx={lx(mi, n)} cy={ly(v)} r={3} fill={s.color} stroke="var(--color-surface)" strokeWidth={1.2} />
                <text x={lx(mi, n) + (mi > n * 0.8 ? -6 : 6)} y={ly(v) + 12} textAnchor={mi > n * 0.8 ? "end" : "start"} fontSize={11} fontWeight={600} fill={s.color}>
                  {Math.round(v * 100)}%
                </text>
              </g>
            );
          })}
          {hover !== null && (
            <g>
              <line x1={lx(hover, n)} x2={lx(hover, n)} y1={PAD.t} y2={H - PAD.b} stroke="var(--color-faint)" strokeWidth={1} strokeDasharray="2 3" />
              <g transform={`translate(${Math.min(lx(hover, n) + 10, W - PAD.r - 170)}, ${PAD.t + 4})`}>
                <rect width={166} height={16 + series.length * 16} rx={6} fill="var(--color-surface)" stroke="var(--color-line)" />
                <text x={8} y={13} fontSize={11} fill="var(--color-faint)">{series[0].points[hover][0].slice(0, 7)} 고점 대비</text>
                {series.map((s, k) => (
                  <text key={s.name} x={8} y={29 + k * 16} fontSize={11.5} fill="var(--color-ink)">
                    <tspan fill={s.color}>●</tspan> {s.name} <tspan fontWeight={600}>{(s.points[hover][1] * 100).toFixed(1)}%</tspan>
                  </text>
                ))}
              </g>
            </g>
          )}
        </svg>
      </div>
      <Legend series={series} />
      {caption && <figcaption className="mt-1.5 text-[12.5px] leading-relaxed text-faint">{caption}</figcaption>}
    </figure>
  );
}

/** 연도별 수익 띠 — 두 줄(공식/보유). 공식이 보유보다 5%p 이상 좋은 해는 굵게. */
export function YearStrip({ rows, sName, bName }: { rows: { year: string; s: number; b: number }[]; sName: string; bName: string }) {
  const cell = (v: number, strong: boolean) => (
    <td className={`px-1 py-1 text-center tabular-nums ${v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted"} ${strong ? "font-bold" : ""}`}
      style={{ background: v > 0 ? `rgba(217,47,69,${Math.min(Math.abs(v), 0.6) * 0.25})` : v < 0 ? `rgba(37,99,235,${Math.min(Math.abs(v), 0.6) * 0.25})` : undefined }}>
      {v > 0 ? "+" : ""}{Math.round(v * 100)}
    </td>
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-[11.5px]">
        <thead>
          <tr className="text-faint">
            <th className="px-1 py-1 text-left font-medium">연도별 수익 %</th>
            {rows.map((r) => <th key={r.year} className="px-1 py-1 text-center font-medium">{r.year.slice(2)}</th>)}
          </tr>
        </thead>
        <tbody>
          <tr><td className="whitespace-nowrap px-1 py-1 font-semibold text-ink">{sName}</td>{rows.map((r) => <React.Fragment key={r.year}>{cell(r.s, r.s - r.b >= 0.05)}</React.Fragment>)}</tr>
          <tr><td className="whitespace-nowrap px-1 py-1 text-muted">{bName}</td>{rows.map((r) => <React.Fragment key={r.year}>{cell(r.b, false)}</React.Fragment>)}</tr>
        </tbody>
      </table>
    </div>
  );
}
