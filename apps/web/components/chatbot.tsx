"use client";

/** 매매 도우미 챗봇 — 우측 슬라이드 패널 (2026-09-04 지시).
 *  플로팅 💬 버튼 → 우측 패널 열림, X 로 닫기. 닫아도 언마운트하지 않아 대화가 유지된다.
 *  백엔드 /chat 은 SSE: tool(진행) → final(답) | error. 대화 이력은 클라이언트가 보관(무상태 서버). */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiFetch } from "../lib/api";

type Msg = { role: "user" | "assistant"; content: string };

/** 봇 답변 마크다운 렌더 (2026-09-04 지시) — raw HTML 은 렌더하지 않음(XSS 차단, react-markdown 기본).
 *  표·굵게·목록·코드가 디자인 토큰으로 스타일링된다. */
function BotMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
      p: (p) => <p className="mb-2 last:mb-0" {...p} />,
      strong: (p) => <strong className="font-bold text-ink" {...p} />,
      ul: (p) => <ul className="mb-2 list-disc pl-5 last:mb-0" {...p} />,
      ol: (p) => <ol className="mb-2 list-decimal pl-5 last:mb-0" {...p} />,
      li: (p) => <li className="mb-0.5" {...p} />,
      h1: (p) => <div className="mb-1.5 mt-2 text-[14px] font-bold first:mt-0" {...p} />,
      h2: (p) => <div className="mb-1.5 mt-2 text-[14px] font-bold first:mt-0" {...p} />,
      h3: (p) => <div className="mb-1 mt-2 text-[13.5px] font-bold first:mt-0" {...p} />,
      a: (p) => <a className="text-accent underline underline-offset-2" target="_blank" rel="noreferrer" {...p} />,
      code: (p) => <code className="rounded bg-raised px-1 py-0.5 text-[12px]" {...p} />,
      pre: (p) => <pre className="mb-2 overflow-x-auto rounded-lg bg-raised p-2.5 text-[12px] last:mb-0" {...p} />,
      table: (p) => <div className="mb-2 overflow-x-auto last:mb-0"><table className="w-full whitespace-nowrap text-[12.5px]" {...p} /></div>,
      thead: (p) => <thead className="text-left text-[11.5px] text-faint" {...p} />,
      th: (p) => <th className="border-b border-line px-2 py-1 font-medium" {...p} />,
      td: (p) => <td className="border-b border-line/40 px-2 py-1" {...p} />,
      hr: () => <hr className="my-2 border-line" />,
      blockquote: (p) => <blockquote className="mb-2 border-l-2 border-line-strong pl-3 text-muted last:mb-0" {...p} />,
    }}>{text}</ReactMarkdown>
  );
}

export default function ChatBot() {
  const [open, setOpen] = useState(false);
  // 상단 바 챗봇 아이콘에서 열기 (2026-09-05 Zenith 상단 바)
  useEffect(() => {
    const h = () => setOpen((v) => !v);
    window.addEventListener("exitme:chat", h);
    return () => window.removeEventListener("exitme:chat", h);
  }, []);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");  // "주문표 조회 중…" 진행 표시
  const listRef = useRef<HTMLDivElement>(null);

  // 패널 폭 — 기본 520px, 왼쪽 가장자리 드래그로 360~840px 조절, localStorage 기억 (2026-09-05 지시)
  const [width, setWidth] = useState(520);
  useEffect(() => {
    try {
      const w = Number(localStorage.getItem("chat_width"));
      if (w >= 360 && w <= 840) setWidth(w);
    } catch { /* 저장값 없으면 기본 */ }
  }, []);
  function startResize(e: React.MouseEvent) {
    e.preventDefault();
    const move = (ev: MouseEvent) => {
      const w = Math.min(840, Math.max(360, window.innerWidth - ev.clientX));
      setWidth(w);
      try { localStorage.setItem("chat_width", String(w)); } catch { /* noop */ }
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, status, open]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content: text }];
    setMsgs(next); setInput(""); setBusy(true); setStatus("생각 중…");
    try {
      const res = await apiFetch("/chat", {
        method: "POST",
        body: JSON.stringify({ messages: next.slice(-20) }),  // 최근 20턴만 전송
      });
      if (!res.ok) {
        const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail;
        setMsgs([...next, { role: "assistant", content: `⚠️ ${detail ?? `오류 (${res.status})`}` }]);
        return;
      }
      // SSE 파싱 — data: {...}\n\n 단위
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let finalText: string | null = null;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 2);
          if (!line.startsWith("data:")) continue;
          const ev = JSON.parse(line.slice(5)) as { type: string; content?: string; label?: string };
          if (ev.type === "tool") setStatus(`${ev.label ?? "도구"} 조회 중…`);
          else if (ev.type === "final") finalText = ev.content ?? "";
          else if (ev.type === "error") finalText = `⚠️ ${ev.content}`;
        }
      }
      setMsgs([...next, { role: "assistant", content: finalText ?? "⚠️ 응답이 비었습니다 — 다시 시도해주세요." }]);
    } catch (e) {
      setMsgs([...next, { role: "assistant", content: `⚠️ 연결 실패: ${String(e)}` }]);
    } finally {
      setBusy(false); setStatus("");
    }
  }

  return (
    <>
      {/* 플로팅 열기 버튼 */}
      <button aria-label="매매 도우미 열기" hidden={open}
        className="fixed bottom-6 right-6 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-xl shadow-lg transition-transform hover:scale-105"
        onClick={() => setOpen(true)}>
        💬
      </button>

      {/* 우측 패널 — 닫혀도 언마운트하지 않음(대화 유지). 폭은 왼쪽 가장자리 드래그로 조절 */}
      <div hidden={!open} style={{ width }}
        className="fixed inset-y-0 right-0 z-40 flex max-w-full flex-col border-l border-line bg-surface shadow-2xl">
        <div role="separator" aria-label="채팅창 폭 조절" onMouseDown={startResize}
          className="absolute inset-y-0 left-0 w-1.5 cursor-col-resize transition-colors hover:bg-accent/40" />
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <span className="flex items-center gap-2 text-[15px] font-bold">
            <span className="inline-block h-2.5 w-2.5 rounded-sm bg-accent" /> 매매 도우미
          </span>
          <span className="flex items-center gap-3">
            {msgs.length > 0 && (
              <button className="text-[12.5px] text-faint hover:text-ink" title="대화 초기화"
                onClick={() => setMsgs([])}>새 대화</button>
            )}
            <button aria-label="닫기" className="text-[18px] leading-none text-muted hover:text-ink"
              onClick={() => setOpen(false)}>✕</button>
          </span>
        </div>

        <div ref={listRef} className="flex-1 overflow-y-auto px-4 py-4">
          {msgs.length === 0 && (
            <div className="text-[13.5px] leading-relaxed text-muted">
              <p className="mb-3">전략·계좌에 대해 물어보세요. 실제 데이터를 조회해 답합니다.</p>
              <div className="grid gap-1.5">
                {["내 포트 자산 현황 요약해줘", "내일 주문표 설명해줘", "이번 주 매매 일지 분석해줘",
                  "그리드 익절 규칙이 뭐야?"].map((q) => (
                  <button key={q} className="rounded-lg border border-line bg-inset px-3 py-2 text-left text-[13px] text-muted transition-colors hover:border-accent hover:text-ink"
                    onClick={() => { setInput(q); }}>{q}</button>
                ))}
              </div>
              <p className="mt-4 text-[11.5px] text-faint">모의·과거 데이터 기반이며 투자 권유가 아닙니다.</p>
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={`mb-3 flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed ${
                m.role === "user" ? "whitespace-pre-wrap bg-accent-dim text-ink" : "border border-line bg-inset text-ink"}`}>
                {m.role === "assistant" ? <BotMarkdown text={m.content} /> : m.content}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-[12.5px] text-faint">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />{status}
            </div>
          )}
        </div>

        <div className="border-t border-line p-3">
          <div className="flex items-end gap-2">
            <textarea rows={2} value={input} placeholder="메시지를 입력하세요…"
              className="input flex-1 resize-none !py-2 text-[13.5px]"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault(); void send();
                }
              }} />
            <button className="btn btn-primary !px-3.5 !py-2" disabled={busy || !input.trim()}
              onClick={() => void send()}>전송</button>
          </div>
        </div>
      </div>
    </>
  );
}
