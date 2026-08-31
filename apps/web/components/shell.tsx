"use client";

/** 앱 셸 — 로그인 화면(/login)에서는 사이드바·안내문 없이 로그인 폼만 표시 (2026-08-31 지시). */
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import NavBar from "./nav";

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (pathname === "/login") {
    return <div className="flex min-h-screen items-center justify-center px-5">{children}</div>;
  }
  return (
    <>
      <NavBar />
      <div className="lg:pl-[210px]">
        <div className="mx-auto max-w-7xl px-5 py-7">{children}</div>
        <footer className="mx-auto max-w-7xl px-5 pb-8 pt-2 text-[12.5px] leading-relaxed text-faint">
          모든 수치는 모의·과거 데이터 기반이며 투자 권유가 아닙니다. 시세는 지연 제공됩니다.
          비용(수수료·세금·슬리피지)은 단순화 모델로 계산됩니다.
        </footer>
      </div>
    </>
  );
}
