import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Chip, Empty, Spinner, Tag } from "../components/ui";
import { apiGet, apiPost, ApiError } from "../lib/api";
import { relTime } from "../lib/format";

type LastBuild = { number: number; result: string; when: string; duration_s: number };

type RunningBuild = { number: number; pipeline: string; params: Record<string, string>; since: string };

type Pipeline = {
  key: string;
  label: string;
  folder: string;
  name: string;
  path: string;
  ready: boolean;
  last_build: LastBuild | null;
  running: RunningBuild[];
  queue: number;
};

type JenkinsResp = { version: string; ready: boolean; pipelines: Pipeline[] };

type Candidate = {
  pipeline: string;
  section: string;
  app: string;
  project: string;
  params: Record<string, string>;
  reason: string;
};

const SECTION_ORDER = ["Build", "Deploy → DEV", "Deploy → QC", "Deploy → UAT", "Deploy → PRD", "Release"];
const FREEFORM = new Set(["branchName", "qccomments"]);
const MAX_ROWS = 40;

function resultTone(result: string): "ok" | "warn" | "err" {
  if (result === "SUCCESS") return "ok";
  if (result === "FAILURE") return "err";
  return "warn";
}

function paramTags(params: Record<string, string>) {
  return Object.entries(params)
    .filter(([k]) => k !== "projectName" && k !== "applicationName")
    .map(([k, v]) => (
      <Tag key={k} title={k}>
        {k}={v || "—"}
      </Tag>
    ));
}

export default function Actions() {
  const [pending, setPending] = useState<Candidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState<{ build: number; label: string } | null>(null);

  const jenkins = useQuery({
    queryKey: ["jenkins"],
    queryFn: () => apiGet<JenkinsResp>("/actions/jenkins"),
    refetchInterval: 30_000,
  });
  const cands = useQuery({
    queryKey: ["action-candidates"],
    queryFn: () => apiGet<Candidate[]>("/actions/candidates"),
  });

  const pick = (c: Candidate) => {
    setPending({ ...c, params: { ...c.params } }); // copy — freeform params are editable
    setSuccess(null);
    setError("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const confirm = async () => {
    if (!pending || busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await apiPost<{ queued: boolean; build_number: number }>("/actions/trigger", {
        pipeline: pending.pipeline,
        params: pending.params,
      });
      setSuccess({ build: res.build_number, label: `${pending.section} — ${pending.app}` });
      setPending(null);
      void jenkins.refetch();
      void cands.refetch();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const bySection = new Map<string, Candidate[]>();
  for (const c of cands.data ?? []) {
    const list = bySection.get(c.section) ?? [];
    list.push(c);
    bySection.set(c.section, list);
  }
  const sections = [
    ...SECTION_ORDER.filter((s) => bySection.has(s)),
    ...[...bySection.keys()].filter((s) => !SECTION_ORDER.includes(s)),
  ];

  return (
    <>
      {success && (
        <Card className="reveal" kicker="TRIGGERED" title="Queued on Jenkins">
          <Chip tone="ok">
            Build <span className="mono">#{success.build}</span> queued — {success.label}
          </Chip>
        </Card>
      )}

      {pending && (
        <Card gold className="reveal" kicker="CONFIRM PIPELINE TRIGGER" title={`${pending.section} — ${pending.app}`}>
          <div className="table-scroll">
            <table className="dt">
              <thead>
                <tr>
                  <th>Parameter</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(pending.params).map(([k, v]) => (
                  <tr key={k}>
                    <td className="mono" style={{ fontSize: 12, color: "var(--ink-2)" }}>{k}</td>
                    <td>
                      {FREEFORM.has(k) ? (
                        <input
                          className="input mono"
                          style={{ fontSize: 12, padding: "4px 8px", width: 260 }}
                          value={v}
                          onChange={(e) =>
                            setPending((p) => (p ? { ...p, params: { ...p.params, [k]: e.target.value } } : p))
                          }
                        />
                      ) : (
                        <span className="mono" style={{ fontSize: 12 }}>{v || "—"}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 12.5, color: "var(--warn)", margin: "12px 0" }}>
            ⚠ This queues a real <span className="mono">{pending.pipeline}</span> run — the server re-validates your
            role, team and version gates before anything fires.
          </div>
          {error && (
            <div style={{ marginBottom: 10 }}>
              <Chip tone="err">{error}</Chip>
            </div>
          )}
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn primary" disabled={busy} onClick={confirm}>
              ✦ {busy ? "Queueing…" : "Confirm trigger"}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => setPending(null)}>
              Cancel
            </button>
          </div>
        </Card>
      )}

      <div className="grid cols-3 reveal">
        {jenkins.isLoading && <Spinner label="Contacting orchestrator…" />}
        {jenkins.data?.pipelines.map((p) => (
          <Card
            key={p.key}
            kicker={`ORCHESTRATOR · ${jenkins.data?.version ?? ""}`}
            title={<span className="mono" style={{ fontSize: 13.5 }}>{p.path}</span>}
          >
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: p.running.length ? 10 : 0 }}>
              <Chip tone={p.ready ? "ok" : "err"}>{p.ready ? "ready" : "offline"}</Chip>
              {p.last_build && (
                <Chip tone={resultTone(p.last_build.result)} title={p.last_build.when}>
                  <span className="mono">#{p.last_build.number}</span>&nbsp;{p.last_build.result.toLowerCase()} ·{" "}
                  {relTime(p.last_build.when)} · {p.last_build.duration_s}s
                </Chip>
              )}
              <Chip tone={p.queue ? "warn" : "skip"}>queue {p.queue}</Chip>
            </div>
            {p.running.map((r) => (
              <div
                key={r.number}
                style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", padding: "4px 0" }}
              >
                <Tag tone="gold">#{r.number} running</Tag>
                {paramTags(r.params)}
              </div>
            ))}
          </Card>
        ))}
      </div>

      {cands.isLoading ? (
        <Spinner label="Computing action candidates…" />
      ) : !cands.data?.length ? (
        <Card className="reveal" kicker="ACTION CANDIDATES" title="Nothing to trigger">
          <Empty>
            No actions available for your role/teams — candidates are computed server-side from your RBAC scope.
          </Empty>
        </Card>
      ) : (
        sections.map((section) => {
          const items = bySection.get(section)!;
          const shown = items.slice(0, MAX_ROWS);
          return (
            <Card
              key={section}
              className="reveal"
              kicker="ACTION CANDIDATES"
              title={section}
              actions={<span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>{items.length}</span>}
            >
              <div className="table-scroll">
                <table className="dt">
                  <thead>
                    <tr>
                      <th>Application</th>
                      <th>Project</th>
                      <th>Reason</th>
                      <th>Parameters</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((c) => (
                      <tr key={`${c.pipeline}:${c.project}:${c.app}:${c.params.targetEnv ?? ""}`}>
                        <td>
                          <strong>{c.app}</strong>
                        </td>
                        <td style={{ color: "var(--ink-2)" }}>{c.project}</td>
                        <td className="mono" style={{ fontSize: 12, color: "var(--ink-2)" }}>{c.reason}</td>
                        <td>
                          <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 6 }}>{paramTags(c.params)}</span>
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <button className="btn sm" onClick={() => pick(c)}>
                            ▶ Queue
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {items.length > MAX_ROWS && (
                <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 8 }}>
                  +{items.length - MAX_ROWS} more not shown — narrow your scope via the role switcher.
                </div>
              )}
            </Card>
          );
        })
      )}
    </>
  );
}
