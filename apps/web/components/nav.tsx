"use client";

/** 좌측 사이드바 내비 — 한국/미국 마켓 그룹 + 설정 (2026-08-31 개편, 토스증권·TradingView 참조).
 *  lg 이상: 고정 사이드바 / 미만: 상단 바 + 슬라이드 오버. */
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

type Item = { href: string; label: string; market?: "KR" | "US" };
type Group = { title: string | null; items: Item[] };

const GROUPS: Group[] = [
  { title: null, items: [
    { href: "/dashboard", label: "대시보드" },
    { href: "/chart", label: "차트" },
  ]},
  { title: "🇰🇷 한국 주식", items: [
    { href: "/signals", label: "주문표", market: "KR" },
    { href: "/simulator", label: "시뮬레이터", market: "KR" },
    { href: "/portfolio", label: "실전매매", market: "KR" },
  ]},
  { title: "🇺🇸 미국 주식", items: [
    { href: "/signals?market=US", label: "주문표", market: "US" },
    { href: "/simulator?market=US", label: "시뮬레이터", market: "US" },
    { href: "/portfolio?market=US", label: "실전매매", market: "US" },
  ]},
  { title: "⚙️ 설정", items: [
    { href: "/settings", label: "일반 설정" },
    { href: "/settings/algorithm", label: "알고리즘 설정" },
  ]},
];

function NavInner({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const sp = useSearchParams();
  const curMarket = sp?.get("market") === "US" ? "US" : "KR";

  function isActive(it: Item): boolean {
    const base = it.href.split("?")[0];
    if (base === "/settings") return pathname === "/settings";
    if (!pathname?.startsWith(base)) return false;
    if (it.market) return curMarket === it.market;
    return true;
  }

  return (
    <nav className="flex h-full flex-col gap-1 overflow-y-auto px-3 py-4">
      <Link href="/dashboard" className="mb-3 flex items-center gap-2 px-2 text-[17px] font-extrabold tracking-tight text-ink" onClick={onNavigate}>
        <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent" />
        StockLab
      </Link>
      {GROUPS.map((g, gi) => (
        <div key={gi} className="mb-1.5">
          {g.title && (
            <div className="mb-1 mt-2 px-2 text-[11.5px] font-bold uppercase tracking-wider text-faint">{g.title}</div>
          )}
          <div className="grid gap-0.5">
            {g.items.map((it) => {
              const active = isActive(it);
              return (
                <Link key={it.href} href={it.href} onClick={onNavigate}
                  className={`rounded-lg px-3 py-2 text-[14.5px] font-medium transition-colors ${
                    active ? "bg-raised font-semibold text-ink" : "text-muted hover:bg-raised/60 hover:text-ink"
                  }`}>
                  {it.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
      <div className="mt-auto grid gap-2 px-2 pt-4">
        <span className="text-[11.5px] leading-relaxed text-faint">모의·지연 시세<br />투자 권유 아님</span>
        <Link href="/login" className="btn btn-ghost !justify-center !py-2 text-[13.5px]" onClick={onNavigate}>로그인</Link>
      </div>
    </nav>
  );
}

export default function NavBar() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  useEffect(() => setOpen(false), [pathname]);
  return (
    <Suspense fallback={null}>
      {/* 데스크톱: 고정 사이드바 */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[210px] border-r border-line bg-[rgba(255,255,255,0.92)] backdrop-blur lg:block">
        <NavInner />
      </aside>
      {/* 모바일: 상단 바 + 슬라이드 오버 */}
      <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-[rgba(255,255,255,0.92)] px-4 backdrop-blur lg:hidden">
        <button aria-label="메뉴" className="btn !px-2.5 !py-1.5" onClick={() => setOpen(true)}>☰</button>
        <Link href="/dashboard" className="flex items-center gap-2 text-[16px] font-extrabold text-ink">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent" />StockLab
        </Link>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-[240px] border-r border-line bg-bg shadow-xl">
            <NavInner onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
    </Suspense>
  );
}
