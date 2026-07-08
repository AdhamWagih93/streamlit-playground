import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { Card, Chip, Empty, Segmented, Spinner, Tag } from "../components/ui";
import { apiGet, apiStream } from "../lib/api";
import { fmtDt } from "../lib/format";
import Topology from "./arch/Topology";
import type { ArchDiff, ArchModel, ArchNode, EnvInfo, Finding, Phase } from "./arch/types";

type Mode = "single" | "compare" | "discover";

const WARN_CARD: CSSProperties = {
  borderColor: "rgba(242, 177, 76, 0.45)",
  background: "linear-gradient(180deg, rgba(242, 177, 76, 0.05), var(--surface-1))",
};

const WARN_TAG: CSSProperties = {
  color: "var(--warn)",
  borderColor: "rgba(242, 177, 76, 0.35)",
  background: "rgba(242, 177, 76, 0.07)",
};

function useModel(env: string, projects: string[], app: string, enabled: boolean) {
  return useQuery({
    queryKey: ["arch-model", env, projects.join(","), app],
    queryFn: () =>
      apiGet<ArchModel>("/architecture/model", { env, projects: projects.join(","), app: app || undefined }),
    enabled,
  });
}

function Legend() {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
      <Tag>▢ service</Tag>
      <Tag>▤ data store</Tag>
      <Tag>▮▮ queue</Tag>
      <Tag>◎ directory</Tag>
      <span className="tag" style={WARN_TAG}>⌁ legacy (dashed)</span>
      <Tag tone="blue">— sync</Tag>
      <Tag tone="teal">┄ async · kafka/amqp</Tag>
    </div>
  );
}

function Row(props: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, padding: "7px 0", borderBottom: "1px solid var(--stroke)" }}>
      <span className="mono" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.12em", color: "var(--ink-3)" }}>
        {props.label}
      </span>
      <span style={{ fontSize: 13, textAlign: "right" }}>{props.children}</span>
    </div>
  );
}

function ProvenancePanel(props: { node: ArchNode | null }) {
  const n = props.node;
  return (
    <Card kicker="PROVENANCE" title={n ? n.label : "Node inspector"}>
      {!n ? (
        <Empty>Click a node to inspect its commit provenance.</Empty>
      ) : !n.provenance ? (
        <>
          <Row label="Kind">
            <Tag>{n.type}</Tag>
          </Row>
          <Empty>Infrastructure target — provenance lives on the services that connect to it.</Empty>
        </>
      ) : (
        <>
          <Row label="Project">{n.project || "—"}</Row>
          <Row label="Commit">
            <Tag>{n.provenance.commit}</Tag>
          </Row>
          <Row label="Author">{n.provenance.author}</Row>
          <Row label="Commit date">
            <span className="mono" style={{ fontSize: 12 }}>{fmtDt(n.provenance.commit_date)}</span>
          </Row>
          <Row label="Deployed">
            <Tag tone="blue">{n.provenance.deployed_version || "—"}</Tag>
          </Row>
          <Row label="Sync">
            {n.provenance.is_head ? <Tag tone="teal">HEAD</Tag> : <Tag tone="err">BEHIND HEAD</Tag>}
          </Row>
          {n.is_legacy && (
            <div style={{ marginTop: 10 }}>
              <Chip tone="warn">Legacy system — EOL runtime, direct-DB coupling</Chip>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function SevTag(props: { sev: Finding["severity"] }) {
  if (props.sev === "HIGH") return <Tag tone="err">HIGH</Tag>;
  if (props.sev === "MED") return <span className="tag" style={WARN_TAG}>MED</span>;
  return <Tag tone="blue">LOW</Tag>;
}

export default function Architecture() {
  const [mode, setMode] = useState<Mode>("single");
  const [env, setEnv] = useState("prd");
  const [projects, setProjects] = useState<string[]>([]);
  const [app, setApp] = useState("");
  const [selected, setSelected] = useState<ArchNode | null>(null);
  const [envA, setEnvA] = useState("dev");
  const [envB, setEnvB] = useState("prd");

  // discover state
  const [steps, setSteps] = useState<string[]>([]);
  const [curStep, setCurStep] = useState(-1);
  const [consoleLines, setConsoleLines] = useState<string[]>([]);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [roadmap, setRoadmap] = useState<Phase[] | null>(null);
  const [running, setRunning] = useState(false);
  const consoleRef = useRef<HTMLDivElement | null>(null);

  const envsQ = useQuery({ queryKey: ["arch-envs"], queryFn: () => apiGet<{ envs: EnvInfo[] }>("/architecture/envs") });
  const baseQ = useModel(env, projects, "", true);
  const focusQ = useModel(env, projects, app, app !== "" && mode === "single");
  const modelA = useModel(envA, projects, "", mode === "compare");
  const modelB = useModel(envB, projects, "", mode === "compare");
  const diffQ = useQuery({
    queryKey: ["arch-diff", envA, envB, projects.join(",")],
    queryFn: () => apiGet<ArchDiff>("/architecture/diff", { envA, envB, projects: projects.join(",") }),
    enabled: mode === "compare",
  });

  useEffect(() => {
    const el = consoleRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [consoleLines]);

  const envInfo = envsQ.data?.envs.find((e) => e.env === env);
  const graph = app && mode === "single" ? focusQ.data : baseQ.data;
  const stats = mode === "compare" ? modelB.data?.stats : graph?.stats;
  const appOptions = (baseQ.data?.nodes ?? [])
    .filter((n) => n.type === "service")
    .map((n) => n.id)
    .sort();

  const toggleProject = (p: string) => {
    setApp("");
    setSelected(null);
    setProjects((cur) => (cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p]));
  };

  async function runDiscover() {
    if (running) return;
    setSteps([]);
    setCurStep(-1);
    setConsoleLines([]);
    setFindings(null);
    setRoadmap(null);
    setRunning(true);
    try {
      await apiStream("/architecture/discover", { env, projects }, (e) => {
        if (e.event === "step") {
          const d = e.data as { index: number; title: string; console_lines: string[] };
          setSteps((prev) => {
            const next = [...prev];
            next[d.index] = d.title;
            return next;
          });
          setCurStep(d.index);
          setConsoleLines((prev) => [...prev, ...d.console_lines]);
        } else if (e.event === "findings") {
          setFindings(e.data as Finding[]);
          setCurStep((c) => c + 1);
        } else if (e.event === "roadmap") {
          setRoadmap(e.data as Phase[]);
        }
      });
    } catch (err) {
      setConsoleLines((prev) => [...prev, `⚠ discovery stream failed: ${String(err)}`]);
    } finally {
      setRunning(false);
    }
  }

  const finished = findings !== null;

  return (
    <>
      {/* ------------------------------------------------ scope bar */}
      <Card className="reveal">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
          {mode === "compare" ? (
            <>
              <select className="input" value={envA} onChange={(e) => setEnvA(e.target.value)} aria-label="Environment A">
                {envsQ.data?.envs.map((x) => (
                  <option key={x.env} value={x.env}>A · {x.env.toUpperCase()}</option>
                ))}
              </select>
              <span style={{ color: "var(--ink-3)", fontSize: 12 }}>vs</span>
              <select className="input" value={envB} onChange={(e) => setEnvB(e.target.value)} aria-label="Environment B">
                {envsQ.data?.envs.map((x) => (
                  <option key={x.env} value={x.env}>B · {x.env.toUpperCase()}</option>
                ))}
              </select>
            </>
          ) : (
            <select
              className="input"
              value={env}
              onChange={(e) => {
                setEnv(e.target.value);
                setApp("");
                setSelected(null);
              }}
              aria-label="Environment"
            >
              {envsQ.data?.envs.map((x) => (
                <option key={x.env} value={x.env}>
                  {x.env.toUpperCase()} · {x.apps} apps
                </option>
              ))}
            </select>
          )}

          {mode === "single" && (
            <select
              className="input"
              value={app}
              onChange={(e) => {
                setApp(e.target.value);
                setSelected(null);
              }}
              aria-label="Focus app"
            >
              <option value="">Focus: whole environment</option>
              {appOptions.map((a) => (
                <option key={a} value={a}>
                  Focus: {a}
                </option>
              ))}
            </select>
          )}

          <div style={{ marginLeft: "auto" }}>
            <Segmented<Mode>
              options={[
                { value: "single", label: "Single" },
                { value: "compare", label: "Compare" },
                { value: "discover", label: "✦ Discover" },
              ]}
              value={mode}
              onChange={setMode}
            />
          </div>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>
          {(envInfo?.projects ?? []).map((p) => {
            const on = projects.includes(p.project);
            return (
              <span
                key={p.project}
                className="chip"
                onClick={() => toggleProject(p.project)}
                style={{
                  cursor: "pointer",
                  ...(on ? { borderColor: "var(--stroke-gold)", color: "var(--gold)", background: "rgba(232,180,74,.08)" } : {}),
                }}
                title={on ? "Click to remove from scope" : "Click to scope to this project"}
              >
                {p.project} <span className="mono" style={{ color: on ? "var(--gold)" : "var(--ink-3)" }}>{p.count}</span>
              </span>
            );
          })}
          {projects.length > 0 && (
            <button className="btn sm ghost" onClick={() => setProjects([])}>
              Clear projects ✕
            </button>
          )}
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginTop: 12, fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-2)" }}>
          {stats ? (
            <span>
              {stats.services} services · {stats.stores} data stores · {stats.deps} dependencies
              {stats.legacy > 0 && (
                <span style={{ color: "var(--warn)" }}> · includes {stats.legacy} legacy system{stats.legacy > 1 ? "s" : ""}</span>
              )}
            </span>
          ) : (
            <span>…</span>
          )}
          {mode === "single" && graph?.capped && (
            <Chip tone="warn" title="Highest-degree and story-bearing nodes are kept">
              graph capped at 60 nodes — scope by project to see everything
            </Chip>
          )}
        </div>
      </Card>

      {/* ------------------------------------------------ single */}
      {mode === "single" && (
        <div className="grid cols-3 reveal">
          <Card
            className="span-2"
            kicker={`TOPOLOGY — ${env.toUpperCase()}`}
            title={app ? `${app} — focused subgraph` : "Environment topology"}
            actions={app ? <button className="btn sm" onClick={() => { setApp(""); setSelected(null); }}>Clear focus ✕</button> : undefined}
          >
            {!graph ? (
              <Spinner label="Reconstructing topology…" />
            ) : graph.nodes.length === 0 ? (
              <Empty>No applications match this scope.</Empty>
            ) : (
              <>
                <Legend />
                <Topology model={graph} idPrefix="tsingle" selectedId={selected?.id ?? null} onSelect={setSelected} />
              </>
            )}
          </Card>
          <ProvenancePanel node={selected} />
        </div>
      )}

      {/* ------------------------------------------------ compare */}
      {mode === "compare" && (
        <>
          <div className="grid cols-2 reveal">
            <Card kicker="ENVIRONMENT A" title={envA.toUpperCase()}>
              {modelA.data ? <Topology model={modelA.data} compact idPrefix="tcmpa" /> : <Spinner />}
            </Card>
            <Card kicker="ENVIRONMENT B" title={envB.toUpperCase()}>
              {modelB.data ? <Topology model={modelB.data} compact idPrefix="tcmpb" /> : <Spinner />}
            </Card>
          </div>

          {!diffQ.data ? (
            <Spinner label="Computing structural diff…" />
          ) : (
            <>
              <div className="grid cols-2 reveal">
                <Card kicker="DRIFT" title={`Only in ${envA.toUpperCase()} — ${diffQ.data.only_a.length}`}>
                  {diffQ.data.only_a.length ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {diffQ.data.only_a.map((a) => (
                        <Chip key={a} tone="warn">{a}</Chip>
                      ))}
                    </div>
                  ) : (
                    <Empty>Nothing exclusive to {envA.toUpperCase()}.</Empty>
                  )}
                </Card>
                <Card kicker="DRIFT" title={`Only in ${envB.toUpperCase()} — ${diffQ.data.only_b.length}`}>
                  {diffQ.data.only_b.length ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {diffQ.data.only_b.map((a) => (
                        <Chip key={a} tone="warn">{a}</Chip>
                      ))}
                    </div>
                  ) : (
                    <Empty>Nothing exclusive to {envB.toUpperCase()}.</Empty>
                  )}
                </Card>
              </div>

              {diffQ.data.repeated_urls.length > 0 && (
                <Card
                  className="reveal"
                  style={WARN_CARD}
                  kicker="ENVIRONMENT ISOLATION"
                  title={<span style={{ color: "var(--warn)" }}>⚠ Environment isolation breach — same endpoint in both</span>}
                >
                  {diffQ.data.repeated_urls.map((r, i) => (
                    <div key={i} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, padding: "6px 0", fontSize: 13 }}>
                      <strong>{r.app}</strong>
                      <span style={{ color: "var(--ink-3)" }}>→</span>
                      <Tag tone="err">{r.endpoint}</Tag>
                      <span style={{ color: "var(--ink-2)" }}>
                        identical connection string in {r.envs.map((e) => e.toUpperCase()).join(" and ")} — a lower environment is
                        pointing at production data.
                      </span>
                    </div>
                  ))}
                </Card>
              )}

              <Card className="reveal" kicker="CONNECTION DRIFT" title={`Changed connections — ${diffQ.data.changed.length} apps`}>
                {diffQ.data.changed.length === 0 ? (
                  <Empty>No connection drift between {envA.toUpperCase()} and {envB.toUpperCase()}.</Empty>
                ) : (
                  <div className="table-scroll" style={{ maxHeight: 460, overflowY: "auto" }}>
                    <table className="dt">
                      <thead>
                        <tr>
                          <th>App</th>
                          <th>Removed ({envA.toUpperCase()} only)</th>
                          <th>Added ({envB.toUpperCase()} only)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {diffQ.data.changed.map((c) => (
                          <tr key={c.app}>
                            <td style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{c.app}</td>
                            <td>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                {c.removed.map((x) => (
                                  <Tag key={x} tone="err">− {x}</Tag>
                                ))}
                              </div>
                            </td>
                            <td>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                {c.added.map((x) => (
                                  <Tag key={x} tone="ok">+ {x}</Tag>
                                ))}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </>
          )}
        </>
      )}

      {/* ------------------------------------------------ discover */}
      {mode === "discover" && (
        <>
          <Card
            gold
            className="reveal"
            kicker="AI — ARCHITECTURE DISCOVERY"
            title={
              <>
                <span className="ai-glyph">⌬</span> Discover architecture
              </>
            }
            actions={
              <button className="btn primary" disabled={running} onClick={runDiscover}>
                {running ? "Discovering…" : "⌬ Discover architecture"}
              </button>
            }
          >
            <div style={{ fontSize: 13, color: "var(--ink-2)", maxWidth: 760 }}>
              Clones every repository in scope from the source-of-truth, reconstructs the {env.toUpperCase()} topology from build
              manifests and config trees, then ranks deviations against engineering standards and proposes a modernisation roadmap.
            </div>

            {steps.length > 0 && (
              <div className="grid cols-3" style={{ marginTop: 16 }}>
                <div className="steps">
                  {steps.map((t, i) => (
                    <div key={i} className={`step ${finished || i < curStep ? "done" : i === curStep && running ? "active" : ""}`}>
                      <span className="s-ring">{finished || i < curStep ? "✓" : i + 1}</span>
                      {t}
                    </div>
                  ))}
                </div>
                <div className="console span-2" ref={consoleRef} style={{ maxHeight: 260, overflowY: "auto" }}>
                  {consoleLines.map((l, i) => (
                    <div key={i} className={l.startsWith("✓") ? "c-ok" : l.startsWith("⚠") ? "c-warn" : l.startsWith("$") ? "c-gold" : "c-dim"}>
                      {l}
                    </div>
                  ))}
                  {running && <span className="caret" />}
                </div>
              </div>
            )}
          </Card>

          {findings && (
            <Card className="reveal" kicker="FINDINGS — SEVERITY RANKED" title={`${findings.length} deviations from engineering standards`}>
              {findings.map((f, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    gap: 12,
                    alignItems: "start",
                    padding: "10px 4px",
                    borderBottom: "1px solid var(--stroke)",
                    animation: "slidein .4s var(--ease) both",
                    animationDelay: `${i * 130}ms`,
                  }}
                >
                  <SevTag sev={f.severity} />
                  <div>
                    <div style={{ fontWeight: 600 }}>
                      {f.title} <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>· {f.app}</span>
                    </div>
                    <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 2 }}>{f.detail}</div>
                  </div>
                </div>
              ))}
            </Card>
          )}

          {roadmap && (
            <div className="grid cols-3 reveal">
              {roadmap.map((p, i) => (
                <Card
                  key={p.phase}
                  kicker={`PHASE ${p.phase} — ${p.name}`}
                  title={p.horizon}
                  style={{ animation: "slidein .45s var(--ease) both", animationDelay: `${i * 140}ms` }}
                >
                  <ul style={{ margin: 0, paddingLeft: 18, color: "var(--ink-2)", fontSize: 13, display: "flex", flexDirection: "column", gap: 7 }}>
                    {p.actions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
