import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Card, Chip, Drawer, Empty, Kpi, Pager, Spinner, Tag } from "../components/ui";
import { apiGet } from "../lib/api";
import { fmtDt, relTime } from "../lib/format";

type TeamCard = {
  team: string;
  members: number;
  roles: string[];
  n_new: number;
  n_updated: number;
  projects: number;
  companies: string[];
};

type Summary = {
  tiles: { teams: number; members: number; departments: number; companies: number };
  last_sync: string;
  teams: TeamCard[];
};

type RosterMember = {
  display_name: string;
  username: string;
  email: string;
  title: string;
  department: string;
  company: string;
  manager: string;
  when_created: string;
  when_changed: string;
  other_teams: string[];
};

type Roster = {
  team: string;
  members: RosterMember[];
  companies: { company: string; count: number }[];
  apps_owned: Record<string, string[]>;
};

type DirRow = {
  display_name: string;
  username: string;
  email: string;
  title: string;
  department: string;
  company: string;
  teams: string[];
  multi_team: boolean;
};

type Directory = { rows: DirRow[]; total: number; page: number; pages: number; teams: string[] };

const ROLE_TONE: Record<string, "blue" | "teal" | ""> = { Developer: "blue", QC: "", Operations: "teal" };
const FIELD_LABEL: Record<string, string> = {
  dev_team: "DEV", qc_team: "QC", uat_team: "UAT",
  prd_team: "PRD", ops_team: "OPS", preprod_team: "PREPROD",
};

function isFresh(iso: string): boolean {
  return Date.now() - new Date(iso).getTime() < 24 * 3600 * 1000;
}

export default function Teams() {
  const [open, setOpen] = useState<string | null>(null);
  const [qRaw, setQRaw] = useState("");
  const [q, setQ] = useState("");
  const [teamFilter, setTeamFilter] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const t = setTimeout(() => { setQ(qRaw); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [qRaw]);

  const summary = useQuery({ queryKey: ["teams-summary"], queryFn: () => apiGet<Summary>("/teams/summary") });
  const roster = useQuery({
    queryKey: ["team-roster", open],
    queryFn: () => apiGet<Roster>(`/teams/${encodeURIComponent(open!)}`),
    enabled: !!open,
  });
  const dir = useQuery({
    queryKey: ["team-members", q, teamFilter, page],
    queryFn: () => apiGet<Directory>("/teams/members/all", { q, team: teamFilter, page, size: 50 }),
    placeholderData: (prev: Directory | undefined) => prev,
  });

  if (summary.isLoading) return <Spinner label="Loading team roster…" />;
  const s = summary.data;

  return (
    <>
      <div className="grid cols-4 reveal">
        <Kpi hero value={s?.tiles.teams ?? 0} label="Engineering teams" delta="LDAP roster · governed" />
        <Kpi value={s?.tiles.members ?? 0} label="Members" />
        <Kpi value={s?.tiles.departments ?? 0} label="Departments" />
        <Kpi value={s?.tiles.companies ?? 0} label="Companies" />
      </div>

      {s && (
        <div className="reveal" style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5, color: "var(--ink-2)" }}>
          <Chip tone={isFresh(s.last_sync) ? "ok" : "warn"}>{isFresh(s.last_sync) ? "FRESH" : "STALE"}</Chip>
          <span>
            Directory last synced <span className="mono">{relTime(s.last_sync)}</span> ({fmtDt(s.last_sync)})
          </span>
        </div>
      )}

      <div className="grid cols-3 reveal">
        {s?.teams.map((t) => (
          <div
            key={t.team}
            className="card"
            style={{ cursor: "pointer", padding: "14px 16px" }}
            onClick={() => setOpen(t.team)}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
              <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 16, letterSpacing: "-0.01em" }}>
                {t.team}
              </div>
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", justifyContent: "flex-end" }}>
                {t.roles.map((r) => <Tag key={r} tone={ROLE_TONE[r] ?? ""}>{r}</Tag>)}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 8 }}>
              <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 26, fontVariantNumeric: "tabular-nums" }}>
                {t.members}
              </span>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>members</span>
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10, alignItems: "center" }}>
              {t.n_new > 0 && <Tag tone="ok" title="joined in the last 90 days">🆕 {t.n_new} new</Tag>}
              {t.n_updated > 0 && <Tag tone="blue" title="profile changed in the last 14 days">✎ {t.n_updated} updated</Tag>}
              <Tag title={t.companies.join(", ")}>{t.projects} project{t.projects === 1 ? "" : "s"}</Tag>
            </div>
          </div>
        ))}
      </div>

      <Card
        className="reveal"
        kicker="DIRECTORY"
        title="All members"
        actions={
          <>
            <input
              className="input"
              placeholder="Search name, email, title…"
              value={qRaw}
              onChange={(e) => setQRaw(e.target.value)}
              style={{ width: 240 }}
            />
            <select
              className="input"
              value={teamFilter}
              onChange={(e) => { setTeamFilter(e.target.value); setPage(1); }}
            >
              <option value="">All teams</option>
              {(dir.data?.teams ?? s?.teams.map((t) => t.team) ?? []).map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </>
        }
      >
        {dir.data?.rows.length ? (
          <>
            <div className="table-scroll">
              <table className="dt">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Teams</th>
                    <th>Department</th>
                    <th>Company</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {dir.data.rows.map((r) => (
                    <tr key={r.username}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{r.display_name}</div>
                        <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{r.title}</div>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {r.teams.map((t) => <Tag key={t}>{t}</Tag>)}
                        </div>
                      </td>
                      <td>{r.department}</td>
                      <td>{r.company}</td>
                      <td>{r.multi_team && <Tag tone="gold">MULTI-TEAM</Tag>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={dir.data.page} pages={dir.data.pages} total={dir.data.total} onPage={setPage} />
          </>
        ) : dir.isLoading ? (
          <Spinner />
        ) : (
          <Empty>No members match.</Empty>
        )}
      </Card>

      <Drawer open={!!open} onClose={() => setOpen(null)}>
        {roster.data ? (
          <>
            <div className="card-kicker">TEAM ROSTER</div>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 22, marginBottom: 10 }}>
              {roster.data.team}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
              {roster.data.companies.map((c) => (
                <Chip key={c.company} tone="skip">{c.company} · {c.count}</Chip>
              ))}
            </div>

            <div className="table-scroll" style={{ marginBottom: 16 }}>
              <table className="dt">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Company</th>
                    <th>Manager</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {roster.data.members.map((m) => (
                    <tr key={m.username}>
                      <td>
                        <div style={{ fontWeight: 600, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                          {m.display_name}
                          {m.other_teams.map((t) => <Tag key={t} title="also member of">{t}</Tag>)}
                        </div>
                        <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                          {m.title} · <span className="mono">{m.email}</span>
                        </div>
                      </td>
                      <td>{m.company}</td>
                      <td>{m.manager || "—"}</td>
                      <td className="mono" style={{ fontSize: 11.5, color: "var(--ink-2)" }}>{relTime(m.when_changed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card-kicker" style={{ marginBottom: 8 }}>APPS OWNED (BY TEAM FIELD)</div>
            {Object.keys(roster.data.apps_owned).length ? (
              Object.entries(roster.data.apps_owned).map(([fieldName, apps]) => (
                <div key={fieldName} style={{ marginBottom: 10 }}>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", letterSpacing: "0.12em", marginBottom: 5 }}>
                    {FIELD_LABEL[fieldName] ?? fieldName.toUpperCase()} · {apps.length}
                  </div>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {apps.map((a) => <Tag key={a} tone="teal">{a}</Tag>)}
                  </div>
                </div>
              ))
            ) : (
              <Empty>This team owns no applications.</Empty>
            )}
          </>
        ) : (
          <Spinner label="Loading roster…" />
        )}
      </Drawer>
    </>
  );
}
