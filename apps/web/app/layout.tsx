import type { Metadata } from "next";
import type { ReactNode } from "react";
import Shell from "../components/shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "StockLab",
  description: "백테스트 · 실전매매 기록 · 자산 대시보드",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-bg font-sans text-[15px] text-ink antialiased">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
