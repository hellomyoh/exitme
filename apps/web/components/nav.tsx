"use client";

/** 좌측 사이드바 내비 — 한국/미국 마켓 그룹 + 설정 (2026-08-31 개편, 토스증권·TradingView 참조).
 *  lg 이상: 고정 사이드바 / 미만: 상단 바 + 슬라이드 오버. */
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ensureSession, fetchMe, logout, type Me } from "../lib/api";
import { MarketFlag } from "./flags";
import { ICON_BY_LABEL, NavIcon } from "./navicons";

type Item = { href: string; label: string; market?: "KR" | "US"; adminOnly?: boolean };
type Group = { title: string | null; flag?: "KR" | "US"; items: Item[] };

const GROUPS: Group[] = [
  { title: null, items: [
    { href: "/dashboard", label: "대시보드" },
    { href: "/chart", label: "차트" },
  ]},
  // 한국/미국 그룹 병합 — 항목별 시장 아이콘으로 구분 (2026-09-05 지시)
  { title: "주식 실전 매매", items: [
    { href: "/signals", label: "주문표", market: "KR" },
    { href: "/simulator", label: "시뮬레이터", market: "KR" },
    { href: "/portfolio", label: "실전매매", market: "KR" },
    { href: "/signals?market=US", label: "주문표", market: "US" },
    { href: "/simulator?market=US", label: "시뮬레이터", market: "US" },
    { href: "/portfolio?market=US", label: "실전매매", market: "US" },
    { href: "/mjournal", label: "매매일지" },
  ]},
  { title: "⚙️ 설정", items: [
    { href: "/settings", label: "일반 설정" },
    { href: "/settings/algorithm", label: "알고리즘 설정" },
    { href: "/settings/accounts", label: "계정 관리", adminOnly: true },
  ]},
];

function NavInner({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const sp = useSearchParams();
  const [loggedIn, setLoggedIn] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    void ensureSession().then(async (ok) => {
      setLoggedIn(ok);
      if (ok) setMe(await fetchMe());
    });
  }, [pathname]);
  const isAdmin = me?.is_admin === true;
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
      <Link href="/dashboard" className="mb-4 flex items-center gap-2.5 px-2 pt-1" onClick={onNavigate}>
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-ink">
          <span className="inline-block h-2.5 w-2.5 rotate-45 rounded-[2px] bg-white" />
        </span>
        <span className="leading-tight">
          <span className="block text-[15.5px] font-extrabold tracking-tight text-ink">ExitMe</span>
          <span className="block text-[9.5px] font-semibold uppercase tracking-[0.18em] text-faint">Auto Trading</span>
        </span>
      </Link>
      {GROUPS.map((g, gi) => (
        <div key={gi} className="mb-1.5">
          {g.title && (
            <div className="mb-1 mt-2 flex items-center gap-1.5 px-2 text-[11.5px] font-bold uppercase tracking-wider text-faint">
              {g.flag ? <MarketFlag market={g.flag} /> : null}{g.title}
            </div>
          )}
          <div className="grid gap-0.5">
            {g.items.filter((it) => !it.adminOnly || isAdmin).map((it) => {
              const active = isActive(it);
              // 시뮬레이터·주문표·실전매매는 메뉴 클릭 시 항상 초기 화면으로 — 진행 중 상태 리셋 (2026-09-01 지시)
              const resettable = it.market !== undefined;
              return (
                <Link key={it.href} href={it.href}
                  onClick={(e) => {
                    if (resettable) {
                      e.preventDefault();
                      const sep = it.href.includes("?") ? "&" : "?";
                      router.push(`${it.href}${sep}r=${Date.now()}`);
                    }
                    onNavigate?.();
                  }}
                  className={`flex items-center gap-2.5 rounded-lg border px-3 py-2 text-[14px] font-medium transition-colors ${
                    active ? "border-line bg-surface font-semibold text-ink shadow-sm"
                           : "border-transparent text-muted hover:bg-surface/70 hover:text-ink"
                  }`}>
                  <NavIcon kind={ICON_BY_LABEL[it.label] ?? "orders"} />
                  <span className="flex-1">{it.label}</span>
                  {it.market && <MarketFlag market={it.market} />}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
      <div className="mt-auto pt-4">
        <span className="block px-2 pb-2 text-[11px] leading-relaxed text-faint">모의·지연 시세 · 투자 권유 아님</span>
        <div className="border-t border-line px-1 pt-3">
          {loggedIn && me ? (
            <div className="flex items-center gap-2.5 rounded-lg px-1.5 py-1">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink text-[12.5px] font-bold uppercase text-white">
                {me.login.slice(0, 1)}
              </span>
              <span className="min-w-0 flex-1 leading-tight">
                <span className="block truncate text-[13.5px] font-semibold text-ink">{me.login}</span>
                <span className="block text-[11px] text-faint">{isAdmin ? "관리자" : "사용자"}</span>
              </span>
              <button aria-label="로그아웃" title="로그아웃" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-raised hover:text-ink"
                onClick={() => { void logout().then(() => { onNavigate?.(); router.push("/login"); }); }}>
                <NavIcon kind="logout" />
              </button>
            </div>
          ) : (
            <Link href="/login" className="btn !w-full !justify-center !py-2 text-[13.5px]" onClick={onNavigate}>로그인</Link>
          )}
        </div>
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
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[210px] border-r border-line bg-inset lg:block">
        <NavInner />
      </aside>
      {/* 모바일: 상단 바 + 슬라이드 오버 */}
      <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-inset px-4 lg:hidden">
        <button aria-label="메뉴" className="btn !px-2.5 !py-1.5" onClick={() => setOpen(true)}>☰</button>
        <Link href="/dashboard" className="flex items-center gap-2 text-[16px] font-extrabold text-ink">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-ink">
            <span className="inline-block h-2 w-2 rotate-45 rounded-[2px] bg-white" />
          </span>ExitMe
        </Link>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-[240px] border-r border-line bg-inset shadow-xl">
            <NavInner onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}
    </Suspense>
  );
}
