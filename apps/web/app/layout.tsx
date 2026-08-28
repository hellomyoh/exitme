import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "StockLab",
  description: "백테스트 · 실전매매 기록 · 자산 대시보드",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      {/* 다크 우선 — 디자인 시스템은 차트 Phase에서 Tailwind v4 + shadcn/ui로 구성 */}
      <body style={{ background: "#111117", color: "#e6e6ea", fontFamily: "system-ui, sans-serif", margin: 0 }}>
        {children}
      </body>
    </html>
  );
}
