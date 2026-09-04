"use client";

/** 본문 내 한국/미국 전환 스위치 (2026-09-05 지시) — 시장은 메뉴가 아니라 페이지 안에서 선택.
 *  기존 ?market= 파라미터·키 리마운트 구조를 그대로 사용한다 (r 토큰으로 상태 초기화). */
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { MarketFlag } from "./flags";

export default function MarketSwitch() {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const cur = sp?.get("market") === "US" ? "US" : "KR";
  return (
    <div className="mb-4 inline-flex overflow-hidden rounded-lg border border-line bg-surface shadow-sm">
      {(["KR", "US"] as const).map((m) => (
        <button key={m}
          onClick={() => { if (m !== cur) router.push(`${pathname}?${m === "US" ? "market=US&" : ""}r=${Date.now()}`); }}
          className={`flex items-center gap-1.5 px-3.5 py-2 text-[13.5px] font-semibold transition-colors ${
            cur === m ? "bg-ink text-white" : "bg-surface text-muted hover:text-ink"}`}>
          <MarketFlag market={m} /> {m === "KR" ? "한국 주식" : "미국 주식"}
        </button>
      ))}
    </div>
  );
}
