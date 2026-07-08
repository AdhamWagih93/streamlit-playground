import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Card, Chip, Drawer, Empty, Kpi, Pager, Segmented, SevTiles, Spinner, Tag } from "../components/ui";
import { apiGet } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDt, relTime, STATUS_TONE } from "../lib/format";

/* ------------------------------------------------------------------ types */
type StageInfo = { version: string; status: string; when: string; by: string };

type Row = {
  application: string;
  project: string;
  company: string;
  app_type: string;
  build_technology: string;
  deploy_technology: string;
  deploy_platform: string;
  teams: Record<string, string[]>;
  stages: Record<string, StageInfo>;
  next_versions: Record<string, string>;
  is_legacy: boolean;
  has_critical: boolean;
  prd_live: boolean;
};

type InventoryResp = { rows: Row[]; total: number; page: number; pages: number };

type Facets = {
  projects: { name: string; count: number }[];
  companies: string[];
  app_types: string[];
  technologies: string[];
  platforms: string[];
  stats: { apps: number; live_prd: number; with_critical: number; projects: number };
};

type SevCounts = { critical: number; high: number; medium: number; low: number };

type Detail = {
  identity: {
    application: string;
    project: string;
    company: string;
    app_type: string;
    build_technology: string;
    deploy_technology: string;
    deploy_platform: string;
    repository: string;
    repo_url: string;
    build_image: string;
    deploy_image: string;
    namespaces: Record<string, string>;
    teams: Record<string, string[]>;
    is_legacy: boolean;
  };
  stages: ({ stage: string } & StageInfo)[];
  next_versions: Record<string, string>;
  recent_deploys: { env: string; version: string; status: string; when: string; user: string; reason: string }[];
  security: { scanner: string; envs: Record<string, { version: string; counts: SevCounts; delta_vs_prd?: SevCounts }> }[];
  stats: Record<string, number>;
  prd_live: boolean;
};

type Sel = { project: string; application: string };
type Sort = "name" | "activity" | "vuln" | "prd" | "live";

const ENV_ORDER = ["dev", "qc", "uat", "prd"] as const;
const SEV_KEYS: (keyof SevCounts)[] = ["critical", "high", "medium", "low"];
const DOT: Record<string, string> = { ok: "var(--ok)", warn: "var(--warn)", err: "var(--err)", skip: "var(--ink-3)" };

/* ------------------------------------------------------------ tiny pieces */
function VersionCell({ s, prefix }: { s?: StageInfo; prefix?: string }) {
  if (!s?.version) return <span style={{ color: "var(--ink-3)" }}>—</span>;
  const tone = STATUS_TONE[(s.status ?? "").toLowerCase()] ?? "skip";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
      {prefix && (
        <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-3)", width: 10 }}>
          {prefix}
        </span>
      )}
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: DOT[tone], flex: "none" }} />
      <span className="mono" style={{ fontSize: 12 }}>{s.version}</span>
      <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{relTime(s.when)}</span>
    </span>
  );
}

function DeltaLine({ delta, env }: { delta: SevCounts; env: string }) {
  if (!SEV_KEYS.some((k) => delta[k] !== 0)) return null;
  return (
    <div className="mono" style={{ fontSize: 11, marginTop: 6, color: "var(--ink-3)" }}>
      Δ vs PROD ({env.toUpperCase()}):{" "}
      {SEV_KEYS.map((k, i) => (
        <span key={k}>
          {i > 0 && " · "}
          {k.slice(0, 4)}{" "}
          <span style={{ color: delta[k] > 0 ? "var(--err)" : delta[k] < 0 ? "var(--teal)" : "var(--ink-3)" }}>
            {delta[k] > 0 ? `+${delta[k]}` : delta[k]}
          </span>
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- the drawer */
function AppDetail({ sel, visibleEnvs }: { sel: Sel; visibleEnvs: string[] }) {
  const detail = useQuery({
    queryKey: ["inventory-app", sel.project, sel.application],
    queryFn: () =>
      apiGet<Detail>(`/inventory/app/${encodeURIComponent(sel.project)}/${encodeURIComponent(sel.application)}`),
  });

  if (detail.isLoading) return <Spinner label="Loading application…" />;
  if (detail.isError || !detail.data) return <Empty>Application not found in your scope.</Empty>;
  const d = detail.data;
  const id = d.identity;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <div className="card-kicker">DELIVERY FLEET · {id.project} · {id.company}</div>
        <div className="card-title" style={{ fontSize: 20, marginBottom: 6 }}>
          {id.application}
          {id.is_legacy && <Tag tone="gold">LEGACY</Tag>}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <Tag>{id.app_type || "app"}</Tag>
          {id.build_technology && <Tag tone="blue">{id.build_technology}</Tag>}
          {id.deploy_technology && <Tag tone="blue">{id.deploy_technology}</Tag>}
          {id.deploy_platform && <Tag>{id.deploy_platform}</Tag>}
          <Chip tone={d.prd_live ? "ok" : "skip"}>{d.prd_live ? "Live in PROD" : "Not in PROD"}</Chip>
        </div>
      </div>

      <Card kicker="IDENTITY" title="Repository & images">
        <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: "8px 12px", fontSize: 12.5, alignItems: "baseline" }}>
          <span className="card-kicker">repo</span>
          <a className="mono" style={{ fontSize: 12, color: "var(--blue)", wordBreak: "break-all" }} href={id.repo_url} target="_blank" rel="noreferrer">
            {id.repo_url}
          </a>
          {id.build_image && (
            <>
              <span className="card-kicker">build image</span>
              <span className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{id.build_image}</span>
            </>
          )}
          {id.deploy_image && (
            <>
              <span className="card-kicker">deploy image</span>
              <span className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{id.deploy_image}</span>
            </>
          )}
          {Object.keys(id.namespaces ?? {}).length > 0 && (
            <>
              <span className="card-kicker">namespaces</span>
              <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {Object.entries(id.namespaces).map(([env, ns]) => (
                  <Tag key={env} title={env.toUpperCase()}>{ns}</Tag>
                ))}
              </span>
            </>
          )}
          {Object.entries(id.teams ?? {}).map(([f, names]) =>
            names?.length ? (
              <span key={f} style={{ display: "contents" }}>
                <span className="card-kicker">{f.replace("_", " ")}</span>
                <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {names.map((n) => (
                    <Tag key={n} tone="teal">{n}</Tag>
                  ))}
                </span>
              </span>
            ) : null,
          )}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>
          {(["commits", "builds", "releases", "deploys", "jira"] as const).map((k) => (
            <Tag key={k}>
              {k === "jira" ? "jira/requests" : k} <strong style={{ color: "var(--ink)" }}>{d.stats?.[k] ?? 0}</strong>
            </Tag>
          ))}
        </div>
      </Card>

      <Card kicker="PIPELINE" title="Stage progression">
        <div className="table-scroll">
          <table className="dt">
            <thead>
              <tr>
                <th>Stage</th>
                <th>Version</th>
                <th>Status</th>
                <th>When</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {d.stages.map((s) => (
                <tr key={s.stage}>
                  <td className="mono" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: s.version ? "var(--ink)" : "var(--ink-3)" }}>
                    {s.stage}
                  </td>
                  <td className="mono" style={{ fontSize: 12 }}>{s.version || "—"}</td>
                  <td>{s.status ? <Chip status={s.status}>{s.status}</Chip> : <span style={{ color: "var(--ink-3)" }}>—</span>}</td>
                  <td className="mono" style={{ fontSize: 11.5, color: "var(--ink-2)" }} title={fmtDt(s.when)}>
                    {s.when ? relTime(s.when) : "—"}
                  </td>
                  <td style={{ fontSize: 12.5, color: "var(--ink-2)" }}>{s.by || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {Object.keys(d.next_versions ?? {}).length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>
            <span className="card-kicker" style={{ alignSelf: "center" }}>next</span>
            {Object.entries(d.next_versions).map(([branch, v]) => (
              <Tag key={branch} tone="blue">
                {branch} → {v}
              </Tag>
            ))}
          </div>
        )}
      </Card>

      <Card kicker="SECURITY POSTURE" title="Scanners — latest reachable stage">
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {d.security.map((sc) => {
            const envsDesc = [...ENV_ORDER].reverse();
            const show = envsDesc.find((e) => sc.envs[e]);
            if (!show)
              return (
                <div key={sc.scanner} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <Tag>{sc.scanner}</Tag>
                  <span style={{ fontSize: 12, color: "var(--ink-3)" }}>no scans for deployed versions</span>
                </div>
              );
            const entry = sc.envs[show];
            const deltaEnv = envsDesc.find((e) => e !== "prd" && sc.envs[e]?.delta_vs_prd);
            const delta = deltaEnv ? sc.envs[deltaEnv].delta_vs_prd : undefined;
            return (
              <div key={sc.scanner}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                  <Tag tone={entry.counts.critical > 0 ? "err" : ""}>{sc.scanner}</Tag>
                  <Tag>{show.toUpperCase()}</Tag>
                  <span className="mono" style={{ fontSize: 12, color: "var(--ink-2)" }}>{entry.version}</span>
                </div>
                <SevTiles compact critical={entry.counts.critical} high={entry.counts.high} medium={entry.counts.medium} low={entry.counts.low} />
                {delta && deltaEnv && <DeltaLine delta={delta} env={deltaEnv} />}
              </div>
            );
          })}
        </div>
      </Card>

      <Card kicker="ROLLOUTS" title="Recent deployments">
        {d.recent_deploys.length ? (
          <div className="table-scroll">
            <table className="dt">
              <thead>
                <tr>
                  <th>Env</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Reason</th>
                  <th>When</th>
                  <th>By</th>
                </tr>
              </thead>
              <tbody>
                {d.recent_deploys
                  .filter((r) => !r.env || visibleEnvs.includes(r.env))
                  .map((r, i) => (
                    <tr key={i}>
                      <td className="mono" style={{ fontSize: 11, textTransform: "uppercase" }}>{r.env}</td>
                      <td className="mono" style={{ fontSize: 12 }}>{r.version}</td>
                      <td><Chip status={r.status}>{r.status}</Chip></td>
                      <td>{r.reason ? <Tag>{r.reason}</Tag> : "—"}</td>
                      <td className="mono" style={{ fontSize: 11.5, color: "var(--ink-2)" }} title={fmtDt(r.when)}>{relTime(r.when)}</td>
                      <td style={{ fontSize: 12.5, color: "var(--ink-2)" }}>{r.user}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No deployments recorded.</Empty>
        )}
      </Card>
    </div>
  );
}

/* --------------------------------------------------------------- the page */
export default function Fleet() {
  const { me } = useAuth();
  const [q, setQ] = useState("");
  const [qd, setQd] = useState("");
  const [projects, setProjects] = useState<string[]>([]);
  const [company, setCompany] = useState("");
  const [platform, setPlatform] = useState("");
  const [sort, setSort] = useState<Sort>("name");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<Sel | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQd(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const projectsKey = projects.join(",");
  useEffect(() => {
    setPage(1);
  }, [qd, projectsKey, company, platform, sort]);

  const facets = useQuery({ queryKey: ["inventory-facets"], queryFn: () => apiGet<Facets>("/inventory/facets") });
  const inv = useQuery({
    queryKey: ["inventory", qd, projectsKey, company, platform, sort, page],
    queryFn: () =>
      apiGet<InventoryResp>("/inventory", {
        q: qd, projects: projectsKey, company, platform, sort, page, size: 50,
      }),
    placeholderData: (prev) => prev,
  });

  const envs = useMemo(
    () => ENV_ORDER.filter((e) => (me?.visible_envs ?? []).includes(e)),
    [me?.visible_envs],
  );
  const stats = facets.data?.stats;

  if (facets.isLoading && inv.isLoading) return <Spinner label="Loading delivery fleet…" />;

  return (
    <>
      <div className="grid cols-4 reveal">
        <Kpi hero value={stats?.apps ?? 0} label="Applications in your scope" delta={`${facets.data?.companies.length ?? 0} companies`} />
        <Kpi value={stats?.live_prd ?? 0} label="Live in PROD" delta={stats ? `${Math.round((stats.live_prd / Math.max(1, stats.apps)) * 100)}% of fleet` : ""} deltaTone="up" />
        <Kpi value={stats?.with_critical ?? 0} label="Apps with critical findings" delta="latest version · any scanner" deltaTone={stats?.with_critical ? "down" : "flat"} />
        <Kpi value={stats?.projects ?? 0} label="Projects" delta="RBAC-scoped" />
      </div>

      <Card className="reveal" style={{ position: "sticky", top: 8, zIndex: 40, padding: "12px 16px" }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <input
            className="input"
            placeholder="Search applications…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ minWidth: 210 }}
          />
          <select
            className="input"
            value=""
            onChange={(e) => e.target.value && setProjects((p) => [...p, e.target.value])}
          >
            <option value="">+ Project…</option>
            {(facets.data?.projects ?? [])
              .filter((p) => !projects.includes(p.name))
              .map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name} ({p.count})
                </option>
              ))}
          </select>
          {projects.map((p) => (
            <Tag key={p} tone="blue">
              {p}{" "}
              <button
                onClick={() => setProjects((cur) => cur.filter((x) => x !== p))}
                style={{ background: "none", border: "none", color: "inherit", cursor: "pointer", padding: 0, font: "inherit" }}
                aria-label={`Remove ${p}`}
              >
                ×
              </button>
            </Tag>
          ))}
          <select className="input" value={company} onChange={(e) => setCompany(e.target.value)}>
            <option value="">All companies</option>
            {(facets.data?.companies ?? []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <select className="input" value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="">All platforms</option>
            {(facets.data?.platforms ?? []).map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <span style={{ flex: 1 }} />
          <Segmented<Sort>
            options={[
              { value: "name", label: "A→Z" },
              { value: "activity", label: "Activity" },
              { value: "vuln", label: "Vulns" },
              { value: "prd", label: "PROD" },
              { value: "live", label: "Live" },
            ]}
            value={sort}
            onChange={setSort}
          />
        </div>
      </Card>

      <Card
        className="reveal"
        kicker="DELIVERY FLEET"
        title="Pipelines inventory"
        actions={
          inv.data ? (
            <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", alignSelf: "center" }}>
              {inv.data.total.toLocaleString()} applications
            </span>
          ) : undefined
        }
      >
        {inv.isLoading ? (
          <Spinner label="Loading inventory…" />
        ) : inv.data?.rows.length ? (
          <>
            <div className="table-scroll">
              <table className="dt">
                <thead>
                  <tr>
                    <th>App</th>
                    <th>Project · Company</th>
                    <th>Type</th>
                    <th>Tech</th>
                    <th>Pipeline</th>
                    {envs.map((e) => (
                      <th key={e}>{e === "prd" ? "PROD" : e.toUpperCase()}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {inv.data.rows.map((r) => (
                    <tr
                      key={`${r.project}/${r.application}`}
                      style={{ cursor: "pointer" }}
                      onClick={() => setSel({ project: r.project, application: r.application })}
                    >
                      <td>
                        <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                          <strong>{r.application}</strong>
                          {r.is_legacy && <Tag tone="gold">LEGACY</Tag>}
                          {r.has_critical && <Tag tone="err" title="Critical findings on latest version">⚠</Tag>}
                        </span>
                      </td>
                      <td style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>
                        {r.project} · {r.company}
                      </td>
                      <td style={{ color: "var(--ink-2)" }}>{r.app_type || "—"}</td>
                      <td>
                        <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
                          {r.build_technology && <Tag tone="blue">{r.build_technology}</Tag>}
                          {r.deploy_technology && <Tag tone="blue">{r.deploy_technology}</Tag>}
                          {r.deploy_platform && <Tag>{r.deploy_platform}</Tag>}
                        </span>
                      </td>
                      <td>
                        <span style={{ display: "inline-flex", flexDirection: "column", gap: 3 }}>
                          <VersionCell s={r.stages["build"]} prefix="B" />
                          <VersionCell s={r.stages["release"]} prefix="R" />
                        </span>
                      </td>
                      {envs.map((e) => (
                        <td key={e}>
                          <VersionCell s={r.stages[e]} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={inv.data.page} pages={inv.data.pages} total={inv.data.total} onPage={setPage} />
          </>
        ) : (
          <Empty>No applications match the current filters.</Empty>
        )}
      </Card>

      <Drawer open={!!sel} onClose={() => setSel(null)}>
        {sel && (
          <>
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
              <button className="btn sm ghost" onClick={() => setSel(null)}>✕ close</button>
            </div>
            <AppDetail sel={sel} visibleEnvs={me?.visible_envs ?? []} />
          </>
        )}
      </Drawer>
    </>
  );
}
