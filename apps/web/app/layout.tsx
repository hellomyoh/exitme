import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";

export const metadata: Metadata = {
  title: "StockLab",
  description: "백테스트 · 실전매매 기록 · 자산 대시보드",
};

const NAV = [
  { href: "/dashboard", label: "대시보드" },
  { href: "/chart", label: "차트" },
  { href: "/simulator", label: "시뮬레이터" },
  { href: "/signals", label: "주문표" },
  { href: "/portfolio", label: "실전매매" },
  { href: "/login", label: "로그인" },
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      {/* 다크 우선 — 디자인 시스템은 차트 Phase에서 Tailwind v4 + shadcn/ui로 구성 */}
      <body style={{ background: "#111117", color: "#e6e6ea", fontFamily: "system-ui, sans-serif", margin: 0 }}>
        <header style={{ display: "flex", gap: 4, alignItems: "center", padding: "8px 16px",
                         borderBottom: "1px solid #22222c", position: "sticky", top: 0,
                         background: "#111117", zIndex: 10, flexWrap: "wrap" }}>
          <Link href="/dashboard" style={{ color: "#e6e6ea", textDecoration: "none", fontWeight: 700, marginRight: 12 }}>
            StockLab
          </Link>
          {NAV.map((n) => (
            <Link key={n.href} href={n.href}
              style={{ color: "#c9c9d1", textDecoration: "none", fontSize: 14, padding: "4px 10px",
                       borderRadius: 6 }}>
              {n.label}
            </Link>
          ))}
          <span style={{ marginLeft: "auto", opacity: 0.4, fontSize: 11 }}>모의·지연 시세 · 투자 권유 아님</span>
        </header>
        {children}
      </body>
    </html>
  );
}
