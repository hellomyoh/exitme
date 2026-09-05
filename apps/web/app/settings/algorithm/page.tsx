"use client";

/** 알고리즘 설정 — 전략 상수 오버라이드: 롤오버 도움말 + 기본값(수정 불가 표시) + 초기화 (2026-08-31 지시). */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ensureSession, fetchMe } from "../../../lib/api";
import { Callout, Card, CardTitle, PageTitle, Tip } from "../../../components/ui";

type Item = {
  key: string; label: string; help: string; group: string; editable: boolean;
  default: number; value: number; min: number; max: number; overridden: boolean;
};

export default function AlgoSettingsPage() {
  const router = useRouter();
  const [items, setItems] = useState<Item[]>([]);
  const [note, setNote] = useState("");
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");

  async function load() {
    const res = await apiFetch("/settings/algorithm");
    if (!res.ok) return;
    const body = (await res.json()) as { items: Item[]; note: string };
    setItems(body.items);
    setNote(body.note);
    setEdit(Object.fromEntries(body.items.filter((i) => i.editable).map((i) => [i.key, String(i.value)])));
  }

  useEffect(() => {
    // 관리자 전용 (2026-09-05 지시: 일반 계정에서 알고리즘 설정 삭제) — 직접 URL 진입도 일반 설정으로 돌려보낸다
    void ensureSession().then(async (ok) => {
      if (!ok) { router.push("/login"); return; }
      const me = await fetchMe();
      if (!me?.is_admin) { router.replace("/settings"); return; }
      void load();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  async function save() {
    setMsg("");
    const values: Record<string, number> = {};
    for (const [k, v] of Object.entries(edit)) {
      const n = Number(v);
      if (!Number.isFinite(n)) { setMsg(`숫자가 아닌 값: ${k}`); return; }
      values[k] = n;
    }
    const res = await apiFetch("/settings/algorithm", { method: "PUT", body: JSON.stringify({ values }) });
    if (res.ok) {
      const out = (await res.json()) as { overridden_keys: string[] };
      setMsg(out.overridden_keys.length > 0
        ? `✅ 저장됨 — 기본값과 다른 항목 ${out.overridden_keys.length}개`
        : "✅ 저장됨 — 모든 값이 기본값과 동일합니다");
      void load();
    } else setMsg(((await res.json()) as { detail?: string }).detail ?? `저장 실패 (${res.status})`);
  }

  async function reset() {
    if (!window.confirm("모든 항목을 기본값으로 초기화할까요?")) return;
    const res = await apiFetch("/settings/algorithm/reset", { method: "POST" });
    if (res.ok) { setMsg("✅ 기본값으로 초기화되었습니다"); void load(); }
  }

  const groups = Array.from(new Set(items.map((i) => i.group)));

  return (
    <main>
      <PageTitle title="알고리즘 설정" sub="RAVG v2.5 전략 상수 — 항목에 마우스를 올리면 설명이 표시됩니다" />
      {note && <Callout icon="🎯"><span className="text-[13.5px]">{note}</span></Callout>}
      {groups.map((g) => (
        <Card key={g} className="mb-4 mt-4">
          <CardTitle>{g}</CardTitle>
          <div className="overflow-x-auto">
            <table className="w-full text-[14.5px]">
              <thead><tr className="border-b border-line text-left text-[13px] text-faint">
                <th className="pb-2 font-medium">항목</th>
                <th className="pb-2 text-right font-medium">기본값</th>
                <th className="pb-2 text-right font-medium">내 값</th>
                <th className="pb-2 pl-4 font-medium">허용 범위</th>
              </tr></thead>
              <tbody>
                {items.filter((i) => i.group === g).map((i) => (
                  <tr key={i.key} className={`border-b border-line/50 last:border-0 ${i.overridden ? "bg-accent/5" : ""}`}>
                    <td className="py-2.5">
                      <Tip tip={<span>{i.help}</span>}>
                        <span className="font-semibold">{i.label}</span>
                        <span className="text-faint"> ⓘ</span>
                      </Tip>
                      {i.overridden && <span className="ml-2 rounded bg-accent/15 px-1.5 py-0.5 text-[11px] font-bold text-accent">변경됨</span>}
                    </td>
                    <td className="table-num py-2.5">
                      <input className="input w-28 text-right opacity-60" value={String(i.default)} disabled />
                    </td>
                    <td className="table-num py-2.5">
                      {i.editable ? (
                        <input className="input w-28 text-right" value={edit[i.key] ?? ""}
                          onChange={(e) => setEdit({ ...edit, [i.key]: e.target.value })} />
                      ) : (
                        <input className="input w-28 text-right opacity-60" value={String(i.value)} disabled title="시장·종목이 정하는 값 — 자동 적용" />
                      )}
                    </td>
                    <td className="py-2.5 pl-4 text-[13px] text-faint">
                      {i.editable ? `${i.min} ~ ${i.max}` : "자동 (수정 불가)"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
      {items.length > 0 && (
        <div className="sticky bottom-4 flex items-center gap-3 rounded-2xl border border-line bg-[rgba(255,255,255,0.95)] px-5 py-3 shadow-lg backdrop-blur">
          <button className="btn btn-primary" onClick={() => void save()}>저장</button>
          <button className="btn" onClick={() => void reset()}>기본값으로 초기화</button>
          {msg && <span className="text-[13.5px] text-muted">{msg}</span>}
          <span className="ml-auto hidden text-[12.5px] text-faint sm:block">⚠️ 상수 변경은 백테스트로 검증 후 사용하세요 — 잘못된 값은 전략을 무력화할 수 있습니다</span>
        </div>
      )}
    </main>
  );
}
