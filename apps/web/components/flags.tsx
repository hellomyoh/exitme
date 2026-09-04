/** 시장 구분용 소형 국기 아이콘 (2026-09-05 지시).
 *  이모지 국기는 Windows Chrome 에서 렌더되지 않아 인라인 SVG 로 그린다.
 *  한국: 위키미디어 공식 태극기 SVG 기하(viewBox −72 −48 144 96, 회전 33.69°, 건곤감리)를
 *  그대로 내장 — 단순화 도안·태극 단독 아이콘은 국기로 읽히지 않아 교체 (2026-09-05 지시). */
export function MarketFlag({ market, className = "" }: { market?: string | null; className?: string }) {
  const cls = `inline-block h-[11px] w-4 shrink-0 rounded-[2px] align-[-1px] ${className}`;
  const ring = { boxShadow: "0 0 0 0.5px rgba(0,0,0,0.18)" } as const;
  if (market === "US") {
    return (
      <svg viewBox="0 0 16 11" aria-label="미국" role="img" className={cls} style={ring}>
        <rect width="16" height="11" fill="#fff" />
        {[0, 2, 4, 6, 8, 10].map((y) => <rect key={y} y={y} width="16" height="1" fill="#b22234" />)}
        <rect width="7" height="6" fill="#3c3b6e" />
      </svg>
    );
  }
  // 대한민국 — 공식 국기 SVG (Wikimedia Commons, public domain) 원본 기하
  return (
    <svg viewBox="-72 -48 144 96" aria-label="한국" role="img" className={cls} style={ring}>
      <path fill="#fff" d="M-72-48v96H72v-96z" />
      <g stroke="#000" strokeWidth="4">
        <path transform="rotate(33.69006752598)" d="M-50-12v24m6 0v-24m6 0v24m76 0V1m0-2v-11m6 0v11m0 2v11m6 0V1m0-2v-11" />
        <path transform="rotate(-33.69006752598)" d="M-50-12v24m6 0V1m0-2v-11m6 0v24m76 0V1m0-2v-11m6 0v24m6 0V1m0-2v-11" />
      </g>
      <g transform="rotate(33.69006752598)">
        <path fill="#cd2e3a" d="M12 0a18 18 0 11-36 0 24 24 0 1148 0" />
        <path fill="#0047a0" d="M-24 0a24 24 0 1048 0A12 12 0 100 0a12 12 0 11-24 0" />
      </g>
    </svg>
  );
}
