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
          {/* 국기 대신 태극 문양 원형 아이콘 — 소형 국기 도안이 국기로 안 읽히던 문제 (2026-09-05 지시) */}
          <rect width="16" height="11" fill="#fff" />
          <path d="M8 1 a4.5 4.5 0 0 1 0 9 a2.25 2.25 0 0 1 0 -4.5 a2.25 2.25 0 0 0 0 -4.5 Z" fill="#0047a0" />
          <path d="M8 10 a4.5 4.5 0 0 1 0 -9 a2.25 2.25 0 0 1 0 4.5 a2.25 2.25 0 0 0 0 4.5 Z" fill="#cd2e3a" />
        </>
      )}
    </svg>
  );
}
