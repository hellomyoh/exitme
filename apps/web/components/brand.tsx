/** ExitMe 브랜드 마크 — 열린 문틀 세 면과 그 사이로 빠져나가는 상승 화살 (마크 개정안 A안, 2026-09-10 채택).
 *
 * 문틀은 `currentColor` 를 따라 놓인 자리의 글자색을 그대로 쓰고, 화살만 브랜드색(--color-brand)으로 고정한다.
 * 밝은 바탕·어두운 바탕 어디에 놓아도 같은 파일 하나로 버티게 하려는 것이다.
 * 파비콘은 CSS 변수를 읽지 못하므로 app/icon.svg 에 같은 경로를 색만 박아 따로 둔다 — 경로를 고치면 둘 다 고쳐야 한다.
 */
export function BrandMark({ className = "h-8 w-8", title }: { className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true} aria-label={title}>
      {title ? <title>{title}</title> : null}
      <path d="M22 5H10a5 5 0 0 0-5 5v12a5 5 0 0 0 5 5h12" fill="none" stroke="currentColor"
        strokeWidth="3.1" strokeLinecap="round" />
      <path d="M14.8 20.2 26.4 8.6" fill="none" stroke="var(--color-brand)" strokeWidth="3.1"
        strokeLinecap="round" />
      <path d="M19.6 7.4h7.6V15" fill="none" stroke="var(--color-brand)" strokeWidth="3.1"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** 로고타입 — Me 만 브랜드색으로 끊어 이름이 두 낱말로 읽히게 하고 마크와 한 덩어리로 묶는다.
 *  조각(fragment)으로 두면 flex gap 이 Exit 과 Me 사이를 벌리므로 한 덩이로 감싼다. */
export function BrandWord({ className }: { className?: string }) {
  return <span className={className}>Exit<span className="text-brand">Me</span></span>;
}
