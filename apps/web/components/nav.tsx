"use client";

/** 좌측 사이드바 내비 — 한국/미국 마켓 그룹 + 설정 (2026-08-31 개편, 토스증권·TradingView 참조).
 *  lg 이상: 고정 사이드바 / 미만: 상단 바 + 슬라이드 오버. */
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { apiFetch, ensureSession, fetchMe, logout, type Me } from "../lib/api";
import { MarketFlag } from "./flags";
import { ICON_BY_LABEL, NavIcon } from "./navicons";

type Item = { href: string; label: string; reset?: boolean; adminOnly?: boolean };
type Group = { title: string | null; flag?: "KR" | "US"; items: Item[] };

const GROUPS: Group[] = [
  { title: null, items: [
    { href: "/dashboard", label: "대시보드" },
    { href: "/chart", label: "차트" },
  ]},
  // 시장은 메뉴가 아니라 본문 스위치로 — 서브메뉴는 기능 3종만 (2026-09-05 지시)
  { title: "주식 실전 매매", items: [
    { href: "/simulator", label: "시뮬레이터", reset: true },
    { href: "/portfolio", label: "실전매매", reset: true },
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
  const [journals, setJournals] = useState<{ id: number; name: string }[]>([]);
  // 배포 확인용 버전 표시 (2026-09-05 지시) — /health 의 version(git describe)·db_revision(alembic)
  const [ver, setVer] = useState<{ version: string; db_revision: string | null; build_time?: string | null } | null>(null);
  const fmtBuild = (iso: string) => {  // UTC → 브라우저 로컬(KST) "MM-DD HH:mm"
    const d = new Date(iso);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  useEffect(() => {
    void ensureSession().then(async (ok) => {
      setLoggedIn(ok);
      if (ok) {
        setMe(await fetchMe());
        const r = await apiFetch("/mjournals");
        if (r.ok) setJournals(((await r.json()) as { items: { id: number; name: string }[] }).items);
      }
      const h = await apiFetch("/health").catch(() => null);
      if (h?.ok) setVer((await h.json()) as { version: string; db_revision: string | null; build_time?: string | null });
    });
  }, [pathname]);
  // 등록된 매매일지가 서브메뉴로 (2026-09-05 지시) — 설정 그룹 앞에 주입
  const groups: Group[] = [
    ...GROUPS.slice(0, -1),
    { title: "매매일지", items: [
      ...journals.map((j) => ({ href: `/mjournal?jid=${j.id}`, label: j.name })),
      { href: "/mjournal?new=1", label: "＋ 새 매매일지" },
    ]},
    GROUPS[GROUPS.length - 1],
  ];
  const isAdmin = me?.is_admin === true;

  function isActive(it: Item): boolean {
    const base = it.href.split("?")[0];
    if (base === "/settings") return pathname === "/settings";
    if (!pathname?.startsWith(base)) return false;
    if (base === "/mjournal") {  // 일지 서브메뉴 — jid 로 개별 활성 (2026-09-05)
      const want = new URLSearchParams(it.href.split("?")[1] ?? "").get("jid");
      return (sp?.get("jid") ?? null) === want;
    }
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
      {groups.map((g, gi) => (
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
              const resettable = it.reset === true;
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
                  <NavIcon kind={ICON_BY_LABEL[it.label] ?? (it.href.startsWith("/mjournal") ? "journal" : "orders")} />
                  <span className="flex-1">{it.label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
      <div className="mt-auto pt-4">
        <span className="block px-2 pb-2 text-[11px] leading-relaxed text-faint">모의·지연 시세 · 투자 권유 아님
          {ver && <span className="block" title="배포 버전(app/VERSION) · DB 마이그레이션 리비전 · 이미지 빌드 시각">
            {ver.version}{ver.db_revision ? ` · db ${ver.db_revision}` : ""}{ver.build_time ? ` · 빌드 ${fmtBuild(ver.build_time)}` : " · 개발(bind mount)"}</span>}</span>
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
