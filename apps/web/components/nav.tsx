"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/dashboard", label: "대시보드" },
  { href: "/chart", label: "차트" },
  { href: "/simulator", label: "시뮬레이터" },
  { href: "/signals", label: "주문표" },
  { href: "/portfolio", label: "실전매매" },
];

export default function NavBar() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-[rgba(10,10,15,0.85)] backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-1 px-4">
        <Link href="/dashboard" className="mr-4 flex items-center gap-2 text-[15px] font-extrabold tracking-tight text-ink">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent" />
          StockLab
        </Link>
        <nav className="flex items-center gap-0.5">
          {NAV.map((n) => {
            const active = pathname?.startsWith(n.href);
            return (
              <Link key={n.href} href={n.href}
                className={`rounded-lg px-3 py-1.5 text-[13.5px] font-medium transition-colors ${
                  active ? "bg-raised text-ink" : "text-muted hover:text-ink"
                }`}>
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden text-[11px] text-faint sm:block">모의·지연 시세 · 투자 권유 아님</span>
          <Link href="/login" className="btn-ghost btn !px-3 !py-1.5 text-[13px]">로그인</Link>
        </div>
      </div>
    </header>
  );
}
