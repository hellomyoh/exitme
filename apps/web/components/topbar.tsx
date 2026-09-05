"use client";

/** 상단 바 — Zenith 스타일 (2026-09-05 지시): 좌측 검색(메뉴·계좌 빠른 이동, Ctrl+K),
 *  우측 검정 주 버튼(새 실전매매)·챗봇 아이콘·아바타(설정). 데스크톱 전용(모바일은 기존 햄버거 바). */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, fetchMe } from "../lib/api";
import { MarketFlag } from "./flags";

type Target = { label: string; href: string; market?: "KR" | "US"; kind: "메뉴" | "계좌" };

const MENU_TARGETS: Target[] = [
  { label: "대시보드", href: "/dashboard", kind: "메뉴" },
  { label: "차트", href: "/chart", kind: "메뉴" },
  { label: "시뮬레이터", href: "/simulator", kind: "메뉴" },
  { label: "실전매매", href: "/portfolio", kind: "메뉴" },
  { label: "일반 설정", href: "/settings", kind: "메뉴" },
  { label: "알고리즘 설정", href: "/settings/algorithm", kind: "메뉴" },
];

export default function TopBar() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [ports, setPorts] = useState<Target[]>([]);
  const [initial, setInitial] = useState("");

  useEffect(() => {
    void fetchMe().then((m) => m && setInitial(m.login.slice(0, 1).toUpperCase()));
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function loadPorts() {
    if (ports.length) return;
    const r = await apiFetch("/portfolios");
    if (r.ok) {
      const items = ((await r.json()) as { items: { id: number; name: string; market?: string }[] }).items;
      setPorts(items.map((p) => ({
        label: p.name, kind: "계좌" as const, market: (p.market ?? "KR") as "KR" | "US",
        href: `/portfolio?${p.market === "US" ? "market=US&" : ""}pid=${p.id}`,
      })));
    }
  }

  const all = [...MENU_TARGETS, ...ports];
  const hits = q.trim() ? all.filter((t) => t.label.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 8) : [];

  function go(t: Target) {
    setQ(""); setOpen(false);
    router.push(t.href.includes("?") ? `${t.href}&r=${Date.now()}` : `${t.href}?r=${Date.now()}`);
  }

  return (
    <header className="sticky top-0 z-20 hidden h-14 items-center gap-3 border-b border-line bg-bg/90 px-5 backdrop-blur lg:flex">
      <div className="relative w-72">
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint">
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4-4" strokeLinecap="round" />
          </svg>
        </span>
        <input ref={inputRef} value={q} placeholder="메뉴·계좌 검색…"
          className="input !w-full !rounded-lg !py-2 !pl-9 !pr-14 text-[13.5px]"
          onFocus={() => { setOpen(true); void loadPorts(); }}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onKeyDown={(e) => { if (e.key === "Enter" && hits[0]) go(hits[0]); }} />
        <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md border border-line bg-inset px-1.5 py-0.5 text-[10.5px] font-semibold text-faint">Ctrl K</span>
        {open && hits.length > 0 && (
          <div className="absolute left-0 top-full z-30 mt-1.5 w-full overflow-hidden rounded-lg border border-line bg-surface shadow-lg">
            {hits.map((t, i) => (
              <button key={i} className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13.5px] hover:bg-inset"
                onMouseDown={(e) => { e.preventDefault(); go(t); }}>
                {t.market && <MarketFlag market={t.market} />}
                <span className="flex-1 truncate">{t.label}</span>
                <span className="text-[11px] text-faint">{t.kind}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <span className="ml-auto flex items-center gap-2">
        <button className="btn btn-primary !rounded-lg !px-3.5 !py-2 text-[13.5px]"
          onClick={() => router.push(`/portfolio?start=1&r=${Date.now()}`)}>＋ 새 실전매매</button>
        <button aria-label="매매 도우미" title="매매 도우미 (챗봇)"
          className="grid h-9 w-9 place-items-center rounded-lg border border-line bg-surface text-muted transition-colors hover:text-ink"
          onClick={() => window.dispatchEvent(new CustomEvent("exitme:chat"))}>
          <svg viewBox="0 0 24 24" className="h-[17px] w-[17px]" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
            <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H9l-4.2 3.2c-.5.4-.8.1-.8-.4z" />
            <path d="M8.5 9.5h7M8.5 12.8h4.5" />
          </svg>
        </button>
        <button aria-label="설정" title="설정" onClick={() => router.push("/settings")}
          className="grid h-9 w-9 place-items-center rounded-full bg-ink text-[12.5px] font-bold text-white">
          {initial || "…"}
        </button>
      </span>
    </header>
  );
}
