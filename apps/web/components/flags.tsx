/** 시장 구분용 소형 국기 아이콘 (2026-09-05 지시).
 *  이모지 국기는 Windows Chrome 에서 렌더되지 않아(문자 KR/US 로 표시) 인라인 SVG 로 그린다.
 *  16×11 단순화 도안 — 소형 표시에서 식별 가능한 수준. */
export function MarketFlag({ market, className = "" }: { market?: string | null; className?: string }) {
  const m = market === "US" ? "US" : "KR";
  return (
    <svg viewBox="0 0 16 11" aria-label={m === "US" ? "미국" : "한국"} role="img"
      className={`inline-block h-[11px] w-4 shrink-0 rounded-[2px] align-[-1px] ${className}`}
      style={{ boxShadow: "0 0 0 0.5px rgba(0,0,0,0.18)" }}>
      {m === "US" ? (
        <>
          <rect width="16" height="11" fill="#fff" />
          {[0, 2, 4, 6, 8, 10].map((y) => <rect key={y} y={y} width="16" height="1" fill="#b22234" />)}
          <rect width="7" height="6" fill="#3c3b6e" />
        </>
      ) : (
        <>
          <rect width="16" height="11" fill="#fff" />
          <path d="M8 2.6 A2.9 2.9 0 0 1 8 8.4 A1.45 1.45 0 0 1 8 5.5 A1.45 1.45 0 0 0 8 2.6 Z" fill="#cd2e3a" />
          <path d="M8 8.4 A2.9 2.9 0 0 1 8 2.6 A1.45 1.45 0 0 1 8 5.5 A1.45 1.45 0 0 0 8 8.4 Z" fill="#0047a0" />
          {/* 건곤감리 단순화 — 좌상·우하 3획 */}
          {[0, 1, 2].map((i) => (
            <g key={i} fill="#1a1a19">
              <rect x={1.6} y={1.8 + i * 1.3} width="3" height="0.7" transform="rotate(-34 3 3)" />
              <rect x={11.4} y={6.2 + i * 1.3} width="3" height="0.7" transform="rotate(-34 13 8)" />
            </g>
          ))}
        </>
      )}
    </svg>
  );
}
