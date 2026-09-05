/** 미니 스파크라인 — KPI 카드 하단 추세 (2026-09-05 지시, Zenith 스타일). 라이브러리 없이 SVG. */
export function Spark({ data, color = "#f97316", className = "mt-2 h-8 w-full" }: {
  data?: number[] | null; color?: string; className?: string;
}) {
  if (!data || data.length < 2) return null;
  const w = 100, h = 28, pad = 2;
  const min = Math.min(...data), max = Math.max(...data);
  const flat = max - min === 0;  // 변동 없는 시리즈(현금 대기 등)는 바닥에 붙이지 않고 가운데에 그린다 (2026-09-05 지시)
  const span = flat ? 1 : max - min;
  const pts = data.map((v, i) => [
    pad + (i / (data.length - 1)) * (w - pad * 2),
    flat ? h / 2 : h - pad - ((v - min) / span) * (h - pad * 2),
  ]);
  const line = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const area = `${line}L${pts[pts.length - 1][0].toFixed(1)},${h}L${pts[0][0].toFixed(1)},${h}Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={className} aria-hidden>
      <path d={area} fill={color} opacity="0.12" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
