/** 사이드바 메뉴 라인 아이콘 — Zenith 스타일 개편 (2026-09-05). stroke=currentColor 소형 아이콘. */

const P = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export function NavIcon({ kind, className = "h-[16px] w-[16px] shrink-0" }: { kind: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden {...P}>
      {kind === "dashboard" && (<>
        <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
        <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
      </>)}
      {kind === "chart" && (<>
        <path d="M3.5 20.5h17" /><path d="M5 16l4-5 3.5 3 5.5-7" />
      </>)}
      {kind === "orders" && (<>
        <rect x="4.5" y="3.5" width="15" height="17" rx="2" />
        <path d="M8.5 8.5h7M8.5 12h7M8.5 15.5h4.5" />
      </>)}
      {kind === "simulator" && (<>
        <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
        <path d="M10 8.8v6.4l5.2-3.2z" />
      </>)}
      {kind === "trade" && (<>
        <rect x="3.5" y="7" width="17" height="13" rx="2" />
        <path d="M8.5 7V5.5A1.5 1.5 0 0 1 10 4h4a1.5 1.5 0 0 1 1.5 1.5V7" /><path d="M3.5 12.5h17" />
      </>)}
      {kind === "settings" && (<>
        <circle cx="12" cy="12" r="3.2" />
        <path d="M12 3.8v2.4M12 17.8v2.4M3.8 12h2.4M17.8 12h2.4M6.2 6.2l1.7 1.7M16.1 16.1l1.7 1.7M17.8 6.2l-1.7 1.7M7.9 16.1l-1.7 1.7" />
      </>)}
      {kind === "sliders" && (<>
        <path d="M4.5 8h15M4.5 16h15" /><circle cx="9.5" cy="8" r="2.2" fill="var(--color-inset)" /><circle cx="15" cy="16" r="2.2" fill="var(--color-inset)" />
      </>)}
      {kind === "users" && (<>
        <circle cx="9.5" cy="8.5" r="3.2" /><path d="M4 19.5c.7-3 2.9-4.5 5.5-4.5s4.8 1.5 5.5 4.5" />
        <circle cx="17" cy="9.5" r="2.4" /><path d="M16.5 14.6c2.1.2 3.4 1.5 4 3.9" />
      </>)}
      {kind === "logout" && (<>
        <path d="M14.5 4h4a1.5 1.5 0 0 1 1.5 1.5v13a1.5 1.5 0 0 1-1.5 1.5h-4" /><path d="M10 8l-4 4 4 4M6.5 12h9" />
      </>)}
    </svg>
  );
}

export const ICON_BY_LABEL: Record<string, string> = {
  "대시보드": "dashboard", "차트": "chart", "주문표": "orders", "시뮬레이터": "simulator",
  "실전매매": "trade", "일반 설정": "settings", "알고리즘 설정": "sliders", "계정 관리": "users",
};
