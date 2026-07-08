import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Card, Chip, Empty, Spinner, Tag } from "../components/ui";
import { apiGet, apiStream } from "../lib/api";
import { relTime } from "../lib/format";

type Incident = {
  id: number;
  app: string;
  project: string;
  company: string;
  version: string;
  env: string;
  when: string;
  failed_stage: string;
  retries: number;
  auto_rollback: boolean;
  requester: string;
  status: string;
  headline: string;
};

type ConsoleLine = { text: string; tone?: string };
type Step = { index: number; label: string; status: "running" | "done" };
type Verdict = {
  root_cause: string;
  confidence: number;
  evidence_sources: number;
  actions: string[];
  impact: string;
  mttr_estimate: string;
  duration_s: number;
};

const TONE_CLASS: Record<string, string> = {
  ok: "c-ok",
  err: "c-err",
  warn: "c-warn",
  dim: "c-dim",
  gold: "c-gold",
};

export default function Incidents() {
  const incidents = useQuery({ queryKey: ["ai-incidents"], queryFn: () => apiGet<Incident[]>("/ai/incidents") });
  const [selId, setSelId] = useState<number | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [lines, setLines] = useState<ConsoleLine[]>([]);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [typed, setTyped] = useState("");
  const [audit, setAudit] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const consoleRef = useRef<HTMLDivElement>(null);

  const list = incidents.data ?? [];
  const sel = list.find((i) => i.id === selId) ?? list[0];

  useEffect(() => {
    if (selId === null && list.length) setSelId(list[0].id);
  }, [list, selId]);

  // Typewriter over the verdict narrative (client-side typing effect).
  useEffect(() => {
    if (!verdict) {
      setTyped("");
      return;
    }
    setTyped("");
    const full = verdict.root_cause;
    let i = 0;
    const t = window.setInterval(() => {
      i = Math.min(full.length, i + 2);
      setTyped(full.slice(0, i));
      if (i >= full.length) window.clearInterval(t);
    }, 16);
    return () => window.clearInterval(t);
  }, [verdict]);
  const typing = verdict ? typed.length < verdict.root_cause.length : false;

  // Auto-scroll the evidence console as lines arrive.
  useEffect(() => {
    const el = consoleRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  const resetAnalysis = () => {
    abortRef.current?.abort();
    setSteps([]);
    setLines([]);
    setVerdict(null);
    setAudit("");
    setError("");
    setRunning(false);
  };

  const select = (id: number) => {
    if (id === sel?.id) return;
    resetAnalysis();
    setSelId(id);
  };

  const analyze = () => {
    if (!sel || running) return;
    resetAnalysis();
    const ctl = new AbortController();
    abortRef.current = ctl;
    setRunning(true);
    apiStream(
      `/ai/incidents/${sel.id}/analyze`,
      {},
      (e) => {
        if (e.event === "step") {
          const d = e.data as Step & { console_lines?: ConsoleLine[] };
          setSteps((prev) => {
            const next = prev.filter((s) => s.index !== d.index);
            next.push({ index: d.index, label: d.label, status: d.status });
            return next.sort((a, b) => a.index - b.index);
          });
          if (d.console_lines?.length) setLines((prev) => [...prev, ...(d.console_lines ?? [])]);
        } else if (e.event === "verdict") {
          setVerdict(e.data as Verdict);
        } else if (e.event === "done") {
          setAudit((e.data as { audit: string }).audit);
        }
      },
      ctl.signal,
    )
      .catch((err: unknown) => {
        if (!ctl.signal.aborted) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!ctl.signal.aborted) {
          setRunning(false);
          void incidents.refetch();
        }
      });
  };

  useEffect(() => () => abortRef.current?.abort(), []);

  if (incidents.isLoading) return <Spinner label="Loading incidents…" />;

  return (
    <div className="grid cols-3 reveal" style={{ alignItems: "start" }}>
      {/* ---------------- left: incident list ---------------- */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: "80vh", overflowY: "auto", paddingRight: 2 }}>
        <div className="card-kicker">FAILED DEPLOYMENTS — RBAC-SCOPED · {list.length}</div>
        {list.map((inc) => (
          <div
            key={inc.id}
            className="card"
            onClick={() => select(inc.id)}
            style={{
              padding: "12px 14px",
              cursor: "pointer",
              borderColor: sel?.id === inc.id ? "var(--stroke-gold)" : undefined,
              boxShadow: sel?.id === inc.id ? "0 0 24px rgba(232,180,74,.08)" : undefined,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
              <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{inc.app}</strong>
              <Chip tone={inc.status === "open" ? "err" : "skip"}>{inc.status.toUpperCase()}</Chip>
            </div>
            <div className="mono" style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 4 }}>
              {inc.version} → {inc.env.toUpperCase()} · #{inc.id}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              <Tag>{inc.failed_stage} ×{inc.retries}</Tag>
              {inc.auto_rollback && <Tag tone="err">AUTO-ROLLBACK</Tag>}
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 8 }}>
              {relTime(inc.when)} · requested by {inc.requester}
            </div>
          </div>
        ))}
        {!list.length && <Empty>No incidents in scope.</Empty>}
      </div>

      {/* ---------------- right: incident detail + analysis ---------------- */}
      <div className="span-2" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {sel ? (
          <>
            <Card
              gold
              kicker={`INCIDENT #${sel.id} — ${sel.project} · ${sel.company}`}
              title={
                <>
                  {sel.app}{" "}
                  <span className="mono" style={{ fontWeight: 400, fontSize: 13, color: "var(--ink-2)" }}>
                    {sel.version} → {sel.env.toUpperCase()}
                  </span>
                </>
              }
              actions={
                <button className="btn primary" onClick={analyze} disabled={running}>
                  <span>✦</span> {running ? "Analyzing…" : verdict ? "Re-analyze with AI" : "Analyze with AI"}
                </button>
              }
            >
              <div style={{ color: "var(--ink-2)", fontSize: 13 }}>{sel.headline}</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                <Tag tone="err">failed: {sel.failed_stage}</Tag>
                <Tag>retries ×{sel.retries}</Tag>
                {sel.auto_rollback && <Tag tone="err">AUTO-ROLLBACK</Tag>}
                <Tag>{relTime(sel.when)}</Tag>
                <Tag>by {sel.requester}</Tag>
              </div>
            </Card>

            {error && (
              <Card kicker="ANALYSIS" title="Analysis failed">
                <div style={{ color: "var(--err)", fontSize: 13 }}>{error}</div>
              </Card>
            )}

            {(running || steps.length > 0) && (
              <Card kicker="EVIDENCE PIPELINE — 4 SOURCES + ON-PREM REASONING" title="Gathering evidence">
                <div className="steps" style={{ marginBottom: 14 }}>
                  {steps.map((s) => (
                    <div key={s.index} className={`step ${s.status === "done" ? "done" : "active"}`}>
                      <span className="s-ring">{s.status === "done" ? "✓" : s.index}</span>
                      <span>{s.label}</span>
                    </div>
                  ))}
                </div>
                <div className="console" ref={consoleRef} style={{ maxHeight: 220, overflowY: "auto" }}>
                  {lines.map((l, i) => (
                    <div key={i} className={TONE_CLASS[l.tone ?? "dim"] ?? "c-dim"}>
                      {l.text}
                    </div>
                  ))}
                  {running && !verdict && <div className="caret" />}
                </div>
              </Card>
            )}

            {verdict && (
              <div className="ai-card reveal">
                <div className="card-kicker" style={{ color: "var(--gold)" }}>
                  AI VERDICT — GROUNDED IN {verdict.evidence_sources} EVIDENCE SOURCES
                </div>
                <div className="card-title" style={{ marginTop: 4 }}>
                  <span className="ai-glyph">✦</span> Root cause
                </div>
                <p style={{ fontSize: 13.5, lineHeight: 1.7, color: "var(--ink)", whiteSpace: "pre-wrap", margin: "0 0 12px" }}>
                  {typed}
                  {typing && <span className="caret" />}
                </p>
                {!typing && (
                  <>
                    <div className="mono" style={{ color: "var(--gold)", fontSize: 12, marginBottom: 14 }}>
                      confidence {verdict.confidence.toFixed(2)} · {verdict.evidence_sources} evidence sources · analyzed in{" "}
                      {verdict.duration_s}s
                    </div>
                    <div className="card-kicker">RECOMMENDED ACTIONS — IN ORDER</div>
                    <ol style={{ margin: "6px 0 14px", paddingLeft: 20, fontSize: 13, lineHeight: 1.7, color: "var(--ink-2)" }}>
                      {verdict.actions.map((a, i) => (
                        <li key={i}>{a}</li>
                      ))}
                    </ol>
                    <div className="grid cols-2" style={{ gap: 10, marginBottom: 14 }}>
                      <div>
                        <div className="card-kicker">IMPACT</div>
                        <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 4 }}>{verdict.impact}</div>
                      </div>
                      <div>
                        <div className="card-kicker">MTTR ESTIMATE</div>
                        <div className="mono" style={{ fontSize: 12.5, color: "var(--ink)", marginTop: 4 }}>
                          {verdict.mttr_estimate}
                        </div>
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                      <button className="btn primary" disabled title="Requires approval flow — not enabled in this environment">
                        Apply fix &amp; redeploy
                      </button>
                      {audit && <Chip tone="ok">{audit}</Chip>}
                    </div>
                  </>
                )}
              </div>
            )}

            {!running && !steps.length && !verdict && (
              <Card kicker="AI — EMBEDDED, NOT BOLTED ON" title="One-click root cause">
                <Empty>
                  ✦ Run the analysis to pull rollout logs, correlate platform events, diff environment configs and check
                  release history — then let the on-prem model reason over the evidence.
                </Empty>
              </Card>
            )}
          </>
        ) : (
          <Card kicker="INCIDENTS" title="Nothing selected">
            <Empty>No failed deployments in your scope. That is the good outcome.</Empty>
          </Card>
        )}
      </div>
    </div>
  );
}
