"use client";

/** 앱 셸 — 로그인 화면(/login)에서는 사이드바 없이 로그인 폼만 표시 (2026-08-31 지시).
 *  인증 게이트: 세션 확인이 끝나기 전에는 페이지 콘텐츠를 렌더하지 않는다 —
 *  비로그인 상태에서 대시보드가 잠깐 노출되던 결함 수정 (2026-09-01 지시). */
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { ensureSession, fetchMe } from "../lib/api";
import ChatBot from "./chatbot";
import NavBar from "./nav";
import TopBar from "./topbar";

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  // null = 확인 중(첫 진입에서만 화면 차단), true = 인증됨
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    if (pathname === "/login") return;
    let alive = true;
    void ensureSession().then(async (ok) => {
      if (!alive) return;
      if (!ok) { router.replace("/login"); return; }  // 미인증 — 콘텐츠를 그린 적 없이 이동
      // 발급 계정 첫 로그인: 비밀번호 변경 전에는 설정 화면만 허용 (2026-09-01 지시)
      const me = await fetchMe();
      if (!alive) return;
      if (me?.must_change_password && pathname !== "/settings") {
        router.replace("/settings?force_pw=1");
        return;
      }
      setAuthed(true);
    });
    return () => { alive = false; };
  }, [pathname, router]);

  if (pathname === "/login") {
    return <div className="flex min-h-screen items-center justify-center px-5">{children}</div>;
  }
  if (authed !== true) {
    // 세션 확인 완료 전 — 대시보드 등 콘텐츠 노출 금지 (짧은 빈 화면 + 로고)
    return (
      <div className="flex min-h-screen items-center justify-center">
        <span className="flex items-center gap-2 text-[15px] font-bold text-faint">
          <span className="inline-block h-2.5 w-2.5 animate-pulse rounded-sm bg-accent" />ExitMe
        </span>
      </div>
    );
  }
  return (
    <>
      <NavBar />
      <div className="lg:pl-[210px]">
        <TopBar />
        {/* overflow-x-clip: 숨은 툴팁·표 등 밖으로 뻗는 요소가 페이지 가로 스크롤을 만들지 않게 (2026-09-02) */}
        <div className="mx-auto max-w-7xl overflow-x-clip px-5 py-7">{children}</div>
        <footer className="mx-auto max-w-7xl px-5 pb-8 pt-2 text-[12.5px] leading-relaxed text-faint">
          모든 수치는 모의·과거 데이터 기반이며 투자 권유가 아닙니다. 시세는 지연 제공됩니다.
          비용(수수료·세금·슬리피지)은 단순화 모델로 계산됩니다.
        </footer>
      </div>
      <ChatBot />
    </>
  );
}
