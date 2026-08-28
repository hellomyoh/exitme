import type { Metadata } from "next";
import type { ReactNode } from "react";
import NavBar from "../components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "StockLab",
  description: "백테스트 · 실전매매 기록 · 자산 대시보드",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-bg font-sans text-[14px] text-ink antialiased">
        <NavBar />
        <div className="mx-auto max-w-6xl px-4 py-6">{children}</div>
        <footer className="mx-auto max-w-6xl px-4 pb-8 pt-2 text-[11px] leading-relaxed text-faint">
          모든 수치는 모의·과거 데이터 기반이며 투자 권유가 아닙니다. 시세는 지연 제공됩니다.
          비용(수수료·세금·슬리피지)은 단순화 모델로 계산됩니다.
        </footer>
      </body>
    </html>
  );
}
