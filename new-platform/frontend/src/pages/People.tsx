import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Empty, Kpi, Pager, Segmented, Spinner, Tag } from "../components/ui";
import { apiGet } from "../lib/api";

type Row = {
  email: string;
  name: string;
  title: string;
  teams: string[];
  unknown: boolean;
  commits: number;
  builds_authored: number;
  deploys_requested: number;
  releases_authored: number;
  requests_made: number;
  approvals: number;
  total: number;
};

type Rollup = {
  team: string;
  members_active: number;
  commits: number;
  deploys: number;
  releases: number;
  total: number;
};

type Summary = {
  window: string;
  tiles: { users: number; with_team: number; commits: number; requests: number; approvals: number };
  rows: Row[];
  total: number;
  page: number;
  pages: number;
  team_rollup: Rollup[];
};

type Win = "7d" | "30d" | "90d" | "180d" | "1y" | "all";

const WINDOWS: { value: Win; label: string }[] = [
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
  { value: "180d", label: "180d" },
  { value: "1y", label: "1y" },
  { value: "all", label: "All" },
];

export default function People() {
  const [win, setWin] = useState<Win>("90d");
  const [page, setPage] = useState(1);

  const q = useQuery({
    queryKey: ["people-summary", win, page],
    queryFn: () => apiGet<Summary>("/people/summary", { window: win, page, size: 50 }),
    placeholderData: (prev: Summary | undefined) => prev,
  });

  if (q.isLoading) return <Spinner label="Aggregating people activity…" />;
  const d = q.data;

  return (
    <>
      <div className="reveal" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div className="card-kicker">ACTIVITY WINDOW</div>
        <Segmented options={WINDOWS} value={win} onChange={(v) => { setWin(v); setPage(1); }} />
      </div>

      <div className="grid reveal" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
        <Kpi hero value={d?.tiles.users ?? 0} label="Active identities" delta={`window · ${win}`} />
        <Kpi value={d?.tiles.with_team ?? 0} label="Matched to a team" />
        <Kpi value={d?.tiles.commits ?? 0} label="Commits" />
        <Kpi value={d?.tiles.requests ?? 0} label="Deploy requests" />
        <Kpi value={d?.tiles.approvals ?? 0} label="Approvals" />
      </div>

      <Card className="reveal" kicker="PER-USER ACTIVITY" title="Who is doing what">
        {d?.rows.length ? (
          <>
            <div className="table-scroll">
              <table className="dt">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Team</th>
                    <th style={{ textAlign: "right" }}>Commits</th>
                    <th style={{ textAlign: "right" }}>Builds</th>
                    <th style={{ textAlign: "right" }}>Deploys</th>
                    <th style={{ textAlign: "right" }}>Releases</th>
                    <th style={{ textAlign: "right" }}>Requests</th>
                    <th style={{ textAlign: "right" }}>Approvals</th>
                    <th style={{ textAlign: "right" }}>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={r.email}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{r.name}</div>
                        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>{r.email}</div>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {r.unknown && <Tag tone="err" title="identity not found in the LDAP roster">UNKNOWN</Tag>}
                          {r.teams.map((t) => <Tag key={t}>{t}</Tag>)}
                        </div>
                      </td>
                      <td className="num" style={{ textAlign: "right" }}>{r.commits.toLocaleString()}</td>
                      <td className="num" style={{ textAlign: "right" }}>{r.builds_authored.toLocaleString()}</td>
                      <td className="num" style={{ textAlign: "right" }}>{r.deploys_requested.toLocaleString()}</td>
                      <td className="num" style={{ textAlign: "right" }}>{r.releases_authored.toLocaleString()}</td>
                      <td className="num" style={{ textAlign: "right" }}>{r.requests_made.toLocaleString()}</td>
                      <td className="num" style={{ textAlign: "right" }}>{r.approvals.toLocaleString()}</td>
                      <td className="num" style={{ textAlign: "right", fontWeight: 700, color: "var(--ink)" }}>
                        {r.total.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={d.page} pages={d.pages} total={d.total} onPage={setPage} />
          </>
        ) : (
          <Empty>No activity in this window.</Empty>
        )}
      </Card>

      <Card className="reveal" kicker="TEAM ROLL-UP" title="Activity by team">
        {d?.team_rollup.length ? (
          <div className="table-scroll">
            <table className="dt">
              <thead>
                <tr>
                  <th>Team</th>
                  <th style={{ textAlign: "right" }}>Active members</th>
                  <th style={{ textAlign: "right" }}>Commits</th>
                  <th style={{ textAlign: "right" }}>Deploys</th>
                  <th style={{ textAlign: "right" }}>Releases</th>
                  <th style={{ textAlign: "right" }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {d.team_rollup.map((t) => (
                  <tr key={t.team}>
                    <td style={{ fontWeight: 600 }}>{t.team}</td>
                    <td className="num" style={{ textAlign: "right" }}>{t.members_active.toLocaleString()}</td>
                    <td className="num" style={{ textAlign: "right" }}>{t.commits.toLocaleString()}</td>
                    <td className="num" style={{ textAlign: "right" }}>{t.deploys.toLocaleString()}</td>
                    <td className="num" style={{ textAlign: "right" }}>{t.releases.toLocaleString()}</td>
                    <td className="num" style={{ textAlign: "right", fontWeight: 700, color: "var(--ink)" }}>
                      {t.total.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No team activity in this window.</Empty>
        )}
      </Card>
    </>
  );
}
