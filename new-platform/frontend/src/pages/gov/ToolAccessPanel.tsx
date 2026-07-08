/** Governance — tool access grants and RBAC audit. */
import { useQuery } from "@tanstack/react-query";

import { Empty, Spinner, Tag } from "../../components/ui";
import { apiGet } from "../../lib/api";
import { DetailsBlock, PanelHead, Tile, Tiles } from "./common";

type Unauthorized = {
  user: string;
  email: string;
  team: string;
  tool: string;
  project: string;
  privilege: string;
  last_updated: string;
  why: string;
};

type ToolAccess = {
  tiles: {
    active_grants: number;
    users: number;
    ado: number;
    jira: number;
    jenkins: number;
    rbac_checked: number;
    unauthorized: number;
  };
  unauthorized: Unauthorized[];
  breakdown: { project: string; ADO: number; JIRA: number; Jenkins: number; total: number }[];
};

const TOOL_TONE: Record<string, "blue" | "teal" | ""> = { ADO: "blue", JIRA: "teal", Jenkins: "" };

export function ToolAccessPanel() {
  const q = useQuery({ queryKey: ["gov", "tool-access"], queryFn: () => apiGet<ToolAccess>("/governance/tool-access") });

  if (q.isLoading) return <Spinner label="Auditing tool grants against team ownership…" />;
  const d = q.data;
  if (!d) return <Empty>Tool-access audit unavailable.</Empty>;

  return (
    <>
      <PanelHead kicker="TOOL ACCESS — GRANTS vs OWNING TEAMS (ADO / JIRA RBAC-CHECKED)" />

      <Tiles min={110}>
        <Tile n={d.tiles.active_grants} label="active grants" />
        <Tile n={d.tiles.users} label="users" />
        <Tile n={d.tiles.ado} label="ADO" tone="blue" />
        <Tile n={d.tiles.jira} label="JIRA" tone="blue" />
        <Tile n={d.tiles.jenkins} label="Jenkins" tone="blue" />
        <Tile n={d.tiles.rbac_checked} label="rbac checked" tone="ok" />
        <Tile n={d.tiles.unauthorized} label="unauthorized" tone={d.tiles.unauthorized ? "err" : "ok"} />
      </Tiles>

      <div className="card" style={{ padding: "14px 18px" }}>
        <div className="card-kicker">UNAUTHORIZED ACCESS · {d.unauthorized.length}</div>
        {d.unauthorized.length ? (
          <div className="table-scroll" style={{ maxHeight: 380, overflowY: "auto", marginTop: 8 }}>
            <table className="dt">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Team</th>
                  <th>Tool</th>
                  <th>Project</th>
                  <th>Privilege</th>
                  <th>Why flagged</th>
                </tr>
              </thead>
              <tbody>
                {d.unauthorized.map((u, i) => (
                  <tr key={`${u.email}-${u.tool}-${u.project}-${i}`}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{u.user}</div>
                      <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>{u.email}</div>
                    </td>
                    <td><Tag>{u.team}</Tag></td>
                    <td><Tag tone={TOOL_TONE[u.tool] ?? ""}>{u.tool}</Tag></td>
                    <td>{u.project}</td>
                    <td className="mono">{u.privilege}</td>
                    <td style={{ color: "var(--warn)", fontSize: 12 }}>{u.why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>Every ADO / JIRA grant maps to an owning team.</Empty>
        )}
      </div>

      <DetailsBlock title="Grant breakdown by project × tool" count={d.breakdown.length} tone="warn">
        <table className="dt">
          <thead>
            <tr>
              <th>Project</th>
              <th style={{ textAlign: "right" }}>ADO</th>
              <th style={{ textAlign: "right" }}>JIRA</th>
              <th style={{ textAlign: "right" }}>Jenkins</th>
              <th style={{ textAlign: "right" }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {d.breakdown.map((b) => (
              <tr key={b.project}>
                <td style={{ fontWeight: 600 }}>{b.project}</td>
                <td className="num" style={{ textAlign: "right" }}>{b.ADO}</td>
                <td className="num" style={{ textAlign: "right" }}>{b.JIRA}</td>
                <td className="num" style={{ textAlign: "right" }}>{b.Jenkins}</td>
                <td className="num" style={{ textAlign: "right", fontWeight: 700, color: "var(--ink)" }}>{b.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DetailsBlock>
    </>
  );
}
