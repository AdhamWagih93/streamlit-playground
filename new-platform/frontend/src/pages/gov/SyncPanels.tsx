/** Governance sync panels: git↔ES inventory, inventory↔Postgres, LDAP roster. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Chip, Empty, Spinner, Tag } from "../../components/ui";
import { apiGet, apiPost } from "../../lib/api";
import { fmtDt, relTime } from "../../lib/format";
import { CleanBanner, DetailsBlock, PanelHead, Tile, Tiles } from "./common";

function RunBtn(props: { pending: boolean; onClick: () => void; label?: string }) {
  return (
    <button className="btn sm" disabled={props.pending} onClick={props.onClick}>
      {props.pending ? <><span className="caret" /> Running…</> : `↻ ${props.label ?? "Re-run"}`}
    </button>
  );
}

// ---------------------------------------------------------------- git ↔ ES
type InvSync = {
  git_total: number;
  es_total: number;
  in_both: number;
  only_git: string[];
  only_es: string[];
  field_diffs: { app: string; field: string; git: string; es: string }[];
  last_run: string;
};

export function SyncInventoryPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["gov", "sync-inv"], queryFn: () => apiGet<InvSync>("/governance/sync/inventory") });
  const run = useMutation({
    mutationFn: () => apiPost<InvSync>("/governance/sync/inventory/run"),
    onSuccess: (data) => qc.setQueryData(["gov", "sync-inv"], data),
  });

  if (q.isLoading) return <Spinner label="Comparing git checkout against search store…" />;
  const d = q.data;
  if (!d) return <Empty>Sync status unavailable.</Empty>;
  const drift = d.only_git.length + d.only_es.length + d.field_diffs.length;

  return (
    <>
      <PanelHead kicker="SOURCE-OF-TRUTH (GIT) ↔ SEARCH STORE (ES)">
        <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>last run {relTime(d.last_run)}</span>
        <RunBtn pending={run.isPending} onClick={() => run.mutate()} />
      </PanelHead>

      <Tiles>
        <Tile n={d.git_total} label="apps in git" />
        <Tile n={d.es_total} label="apps in ES" />
        <Tile n={d.in_both} label="in both" tone="ok" />
        <Tile n={d.only_git.length} label="only in git" tone={d.only_git.length ? "warn" : undefined} />
        <Tile n={d.only_es.length} label="only in ES" tone={d.only_es.length ? "warn" : undefined} />
        <Tile n={d.field_diffs.length} label="field diffs" tone={d.field_diffs.length ? "err" : undefined} />
      </Tiles>

      {drift === 0 && <CleanBanner>Stores are in perfect agreement — no drift detected.</CleanBanner>}

      {(d.only_git.length > 0 || d.only_es.length > 0) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {d.only_git.map((a) => <Chip key={`g-${a}`} tone="warn">GIT ONLY · <span className="mono">{a}</span></Chip>)}
          {d.only_es.map((a) => <Chip key={`e-${a}`} tone="warn">ES ONLY · <span className="mono">{a}</span></Chip>)}
        </div>
      )}

      {d.field_diffs.map((f) => (
        <details key={`${f.app}-${f.field}`} className="card" style={{ padding: "12px 16px" }}>
          <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 13 }}>
            <span className="mono">{f.app}</span> · field <Tag tone="err">{f.field}</Tag> disagrees
          </summary>
          <div className="table-scroll" style={{ marginTop: 10 }}>
            <table className="dt">
              <thead>
                <tr><th>Field</th><th>Git</th><th>ES</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td className="mono">{f.field}</td>
                  <td className="mono" style={{ color: "var(--ok)" }}>{f.git || "—"}</td>
                  <td className="mono" style={{ color: "var(--err)" }}>{f.es || "—"}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      ))}
    </>
  );
}

// ---------------------------------------------------------------- inventory ↔ PG
type PgSync = {
  inventory_projects: number;
  postgres_projects: number;
  only_inventory: string[];
  only_postgres: string[];
  team_diffs: { project: string; field: string; inventory: string; postgres: string }[];
  ops_inconsistent: { project: string; uat_team: string; prd_team: string; preprod_team: string }[];
  last_run: string;
};

export function SyncPostgresPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["gov", "sync-pg"], queryFn: () => apiGet<PgSync>("/governance/sync/postgres") });
  const run = useMutation({
    mutationFn: () => apiPost<PgSync>("/governance/sync/postgres/run"),
    onSuccess: (data) => qc.setQueryData(["gov", "sync-pg"], data),
  });

  if (q.isLoading) return <Spinner label="Comparing inventory against devops_projects…" />;
  const d = q.data;
  if (!d) return <Empty>Sync status unavailable.</Empty>;
  const drift = d.only_inventory.length + d.only_postgres.length + d.team_diffs.length + d.ops_inconsistent.length;

  return (
    <>
      <PanelHead kicker="INVENTORY ↔ RELATIONAL STORE (DEVOPS_PROJECTS)">
        <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>last run {relTime(d.last_run)}</span>
        <RunBtn pending={run.isPending} onClick={() => run.mutate()} />
      </PanelHead>

      <Tiles>
        <Tile n={d.inventory_projects} label="inventory projects" />
        <Tile n={d.postgres_projects} label="postgres projects" />
        <Tile n={d.only_inventory.length} label="only in inventory" tone={d.only_inventory.length ? "warn" : undefined} />
        <Tile n={d.only_postgres.length} label="only in postgres" tone={d.only_postgres.length ? "warn" : undefined} />
        <Tile n={d.team_diffs.length} label="team diffs" tone={d.team_diffs.length ? "err" : undefined} />
        <Tile n={d.ops_inconsistent.length} label="ops inconsistent" tone={d.ops_inconsistent.length ? "warn" : undefined} />
      </Tiles>

      {drift === 0 && <CleanBanner>Inventory and Postgres agree — no drift detected.</CleanBanner>}

      {(d.only_inventory.length > 0 || d.only_postgres.length > 0) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {d.only_inventory.map((p) => <Chip key={`i-${p}`} tone="warn">INVENTORY ONLY · <span className="mono">{p}</span></Chip>)}
          {d.only_postgres.map((p) => <Chip key={`p-${p}`} tone="warn">POSTGRES ONLY · <span className="mono">{p}</span></Chip>)}
        </div>
      )}

      {d.team_diffs.map((f) => (
        <details key={`${f.project}-${f.field}`} className="card" style={{ padding: "12px 16px" }}>
          <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 13 }}>
            <span className="mono">{f.project}</span> · field <Tag tone="err">{f.field}</Tag> disagrees
          </summary>
          <div className="table-scroll" style={{ marginTop: 10 }}>
            <table className="dt">
              <thead>
                <tr><th>Field</th><th>Inventory</th><th>Postgres</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td className="mono">{f.field}</td>
                  <td className="mono" style={{ color: "var(--ok)" }}>{f.inventory || "—"}</td>
                  <td className="mono" style={{ color: "var(--err)" }}>{f.postgres || "—"}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </details>
      ))}

      {d.ops_inconsistent.length > 0 && (
        <DetailsBlock title="Ops team inconsistencies (uat / prd / preprod disagree)" count={d.ops_inconsistent.length}>
          <table className="dt">
            <thead>
              <tr><th>Project</th><th>UAT team</th><th>PRD team</th><th>PREPROD team</th></tr>
            </thead>
            <tbody>
              {d.ops_inconsistent.map((o) => (
                <tr key={o.project}>
                  <td className="mono">{o.project}</td>
                  <td>{o.uat_team || <span style={{ color: "var(--err)" }}>unset</span>}</td>
                  <td>{o.prd_team || <span style={{ color: "var(--err)" }}>unset</span>}</td>
                  <td>{o.preprod_team || <span style={{ color: "var(--err)" }}>unset</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DetailsBlock>
      )}
    </>
  );
}

// ---------------------------------------------------------------- LDAP
type LdapSync = {
  last_sync: string;
  status: string;
  users: number;
  teams: number;
  added_users: string[];
  removed_users: string[];
  field_changes: { user: string; field: string; old: string; new: string }[];
  added_memberships: { user: string; team: string }[];
  removed_memberships: { user: string; team: string }[];
};

export function LdapPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["gov", "sync-ldap"], queryFn: () => apiGet<LdapSync>("/governance/sync/ldap") });
  const run = useMutation({
    mutationFn: () => apiPost<LdapSync>("/governance/sync/ldap/run"),
    onSuccess: (data) => qc.setQueryData(["gov", "sync-ldap"], data),
  });

  if (q.isLoading) return <Spinner label="Reading roster sync state…" />;
  const d = q.data;
  if (!d) return <Empty>LDAP sync status unavailable.</Empty>;
  const fresh = Date.now() - new Date(d.last_sync).getTime() < 24 * 3600 * 1000;

  return (
    <>
      <PanelHead kicker="IDENTITY DIRECTORY → ROSTER CACHE">
        <RunBtn pending={run.isPending} onClick={() => run.mutate()} label="Re-sync" />
      </PanelHead>

      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: 13, color: "var(--ink-2)" }}>
        <Chip tone={fresh ? "ok" : "warn"}>{fresh ? "FRESH" : "STALE"}</Chip>
        <Chip status={d.status}>{d.status.toUpperCase()}</Chip>
        <span>
          Last sync <span className="mono">{relTime(d.last_sync)}</span> ({fmtDt(d.last_sync)}) ·{" "}
          <strong>{d.users}</strong> users · <strong>{d.teams}</strong> teams
        </span>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {d.added_users.map((u) => <Chip key={`a-${u}`} tone="ok">+ {u}</Chip>)}
        {d.removed_users.map((u) => <Chip key={`r-${u}`} tone="err">− {u}</Chip>)}
        {d.added_memberships.map((m, i) => <Chip key={`am-${i}`} tone="ok">{m.user} ⇒ + <span className="mono">{m.team}</span></Chip>)}
        {d.removed_memberships.map((m, i) => <Chip key={`rm-${i}`} tone="err">{m.user} ⇒ − <span className="mono">{m.team}</span></Chip>)}
        {!d.added_users.length && !d.removed_users.length && !d.added_memberships.length && !d.removed_memberships.length && (
          <Chip tone="ok">No roster deltas in the last sync</Chip>
        )}
      </div>

      {d.field_changes.length > 0 && (
        <DetailsBlock title="Profile field changes" count={d.field_changes.length}>
          <table className="dt">
            <thead>
              <tr><th>User</th><th>Field</th><th>Old</th><th>New</th></tr>
            </thead>
            <tbody>
              {d.field_changes.map((f, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{f.user}</td>
                  <td className="mono">{f.field}</td>
                  <td style={{ color: "var(--ink-3)" }}>{f.old}</td>
                  <td style={{ color: "var(--ok)" }}>{f.new}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DetailsBlock>
      )}
    </>
  );
}
