import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Card, Segmented, Spinner, Tag } from "../components/ui";
import { apiGet, apiStream } from "../lib/api";

type Persona = "developer" | "analyst" | "tester";

type Sources = {
  categories: { name: string; count: number }[];
  total: number;
  grounded: boolean;
  model: string;
};

type Stats = { questions_this_month: number; teams: number };

type Msg =
  | { kind: "user"; text: string }
  | { kind: "ai"; text: string; citations: string[]; streaming: boolean }
  | { kind: "divider"; text: string };

const PERSONAS: { value: Persona; label: string; suggestion: string }[] = [
  { value: "developer", label: "Developer", suggestion: "How does payments-gateway authenticate to ledger-service?" },
  { value: "analyst", label: "Business Analyst", suggestion: "Draft the BRD scope for instalment payments." },
  { value: "tester", label: "Tester", suggestion: "Generate a regression suite for the next payments-gateway release." },
];

const GOVERNANCE = [
  "Zero data egress — the model runs on-prem",
  "Scoped by identity — RBAC applies to every answer",
  "Fully audited — each exchange is logged",
  "Every answer cites its sources",
];

export default function Assistant() {
  const sources = useQuery({ queryKey: ["ai-sources"], queryFn: () => apiGet<Sources>("/ai/assistant/sources") });
  const stats = useQuery({ queryKey: ["ai-stats"], queryFn: () => apiGet<Stats>("/ai/assistant/stats") });
  const [persona, setPersona] = useState<Persona>("developer");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const personaMeta = PERSONAS.find((p) => p.value === persona) ?? PERSONAS[0];

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const patchLastAi = (fn: (m: Extract<Msg, { kind: "ai" }>) => Msg) => {
    setMessages((prev) =>
      prev.map((m, i) => (i === prev.length - 1 && m.kind === "ai" ? fn(m) : m)),
    );
  };

  const send = (text: string) => {
    const q = text.trim();
    if (!q || streaming) return;
    setInput("");
    const history: { role: string; content: string }[] = [];
    for (const m of messages) {
      if (m.kind === "user") history.push({ role: "user", content: m.text });
      else if (m.kind === "ai" && m.text) history.push({ role: "assistant", content: m.text });
    }
    history.push({ role: "user", content: q });
    setMessages((prev) => [...prev, { kind: "user", text: q }, { kind: "ai", text: "", citations: [], streaming: true }]);
    setStreaming(true);
    apiStream("/ai/assistant/chat", { messages: history, persona }, (e) => {
      if (e.event === "token") {
        const t = (e.data as { text: string }).text;
        patchLastAi((m) => ({ ...m, text: m.text + t }));
      } else if (e.event === "citations") {
        const docs = (e.data as { documents: string[] }).documents ?? [];
        patchLastAi((m) => ({ ...m, citations: docs }));
      } else if (e.event === "error") {
        const d = (e.data as { detail: string }).detail;
        patchLastAi((m) => ({ ...m, text: m.text ? `${m.text}\n[${d}]` : `[${d}]` }));
      }
    })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        patchLastAi((m) => ({ ...m, text: m.text || `Request failed: ${msg}` }));
      })
      .finally(() => {
        patchLastAi((m) => ({ ...m, streaming: false }));
        setStreaming(false);
        void stats.refetch();
      });
  };

  const switchPersona = (p: Persona) => {
    if (p === persona) return;
    setPersona(p);
    const label = PERSONAS.find((x) => x.value === p)?.label ?? p;
    setMessages((prev) => [...prev, { kind: "divider", text: `persona → ${label}` }]);
  };

  return (
    <div className="grid cols-3 reveal" style={{ alignItems: "start" }}>
      {/* ---------------- chat column ---------------- */}
      <Card
        gold
        className="span-2"
        kicker="KNOWLEDGE ASSISTANT — DOC-GROUNDED · ON-PREM"
        title={
          <>
            <span className="ai-glyph">✧</span> Ask the platform
          </>
        }
      >
        <div
          ref={scrollRef}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            minHeight: 360,
            maxHeight: "58vh",
            overflowY: "auto",
            padding: "4px 2px",
          }}
        >
          {messages.length === 0 && (
            <div className="empty">
              Grounded answers over {sources.data ? sources.data.total.toLocaleString() : "the"} internal documents —
              architecture, runbooks, standards, BRDs.
              <br />
              Pick a persona and ask, or use the suggested question.
            </div>
          )}
          {messages.map((m, i) =>
            m.kind === "divider" ? (
              <div
                key={i}
                className="mono"
                style={{ textAlign: "center", fontSize: 11, color: "var(--ink-3)", letterSpacing: ".08em" }}
              >
                — {m.text} —
              </div>
            ) : m.kind === "user" ? (
              <div
                key={i}
                style={{
                  alignSelf: "flex-end",
                  maxWidth: "78%",
                  background: "var(--surface-2)",
                  border: "1px solid var(--stroke)",
                  borderRadius: "12px 12px 4px 12px",
                  padding: "9px 13px",
                  fontSize: 13.5,
                  whiteSpace: "pre-wrap",
                }}
              >
                {m.text}
              </div>
            ) : (
              <div key={i} style={{ alignSelf: "flex-start", maxWidth: "88%", display: "flex", gap: 10 }}>
                <span className="ai-glyph" style={{ fontSize: 15, marginTop: 8 }}>
                  ✦
                </span>
                <div className="ai-card" style={{ padding: "10px 14px", flex: 1 }}>
                  <div style={{ fontSize: 13.5, lineHeight: 1.65, whiteSpace: "pre-wrap" }}>
                    {m.text}
                    {m.streaming && <span className="caret" />}
                  </div>
                  {m.citations.length > 0 && (
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
                      {m.citations.map((c) => (
                        <Tag key={c} tone="gold">
                          ▸ {c}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ),
          )}
        </div>
        <div style={{ display: "flex", gap: 10, marginTop: 14, alignItems: "flex-end" }}>
          <textarea
            className="input"
            rows={2}
            style={{ flex: 1, resize: "none" }}
            value={input}
            placeholder={`Ask as ${personaMeta.label}… (Enter to send · Shift+Enter for newline)`}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
          />
          <button className="btn primary" disabled={streaming || !input.trim()} onClick={() => send(input)}>
            Ask ▸
          </button>
        </div>
      </Card>

      {/* ---------------- sidebar ---------------- */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <Card kicker="PERSONA" title="Answering as">
          <Segmented
            options={PERSONAS.map((p) => ({ value: p.value, label: p.label }))}
            value={persona}
            onChange={switchPersona}
          />
          <button
            className="btn sm ghost"
            style={{ marginTop: 12, textAlign: "left", whiteSpace: "normal", lineHeight: 1.5 }}
            onClick={() => send(personaMeta.suggestion)}
            disabled={streaming}
            title="Send the suggested question"
          >
            <span className="ai-glyph">✧</span> “{personaMeta.suggestion}”
          </button>
        </Card>

        <Card kicker="KNOWLEDGE SOURCES" title="Grounding corpus">
          {sources.data ? (
            <>
              {sources.data.categories.map((c) => (
                <div
                  key={c.name}
                  style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12.5, padding: "3px 0", color: "var(--ink-2)" }}
                >
                  <span>{c.name}</span>
                  <span className="mono">{c.count.toLocaleString()}</span>
                </div>
              ))}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12, alignItems: "center" }}>
                <Tag tone="teal">{sources.data.total.toLocaleString()} docs{sources.data.grounded ? " · grounded" : ""}</Tag>
                <Tag tone="gold">{sources.data.model}</Tag>
              </div>
            </>
          ) : (
            <Spinner />
          )}
        </Card>

        <Card kicker="GOVERNANCE" title="Why not a public chatbot?">
          <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
            {GOVERNANCE.map((t) => (
              <div key={t} style={{ display: "flex", gap: 8, fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.45 }}>
                <span style={{ color: "var(--teal)", flex: "none" }}>✓</span>
                {t}
              </div>
            ))}
          </div>
        </Card>

        {stats.data && (
          <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", textAlign: "center" }}>
            {stats.data.questions_this_month.toLocaleString()} questions this month · {stats.data.teams} teams
          </div>
        )}
      </div>
    </div>
  );
}
