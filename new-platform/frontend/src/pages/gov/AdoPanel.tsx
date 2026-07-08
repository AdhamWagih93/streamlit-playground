/** Governance — Azure DevOps pipeline coverage panel. */
import { useQuery } from "@tanstack/react-query";

import { Chip, Empty, Spinner, Tag } from "../../components/ui";
import { apiGet } from "../../lib/api";
import { DetailsBlock, PanelHead, Tile, Tiles } from "./common";

type Ado = {
  headline: { apps_total: number; pipelined: number; pct: number };
  tiles: {
    pipelined: number;
    no_repo: number;
    hooks_complete: number;
    missing_hooks: number;
    team_mismatch: number;
    azure_pipelines: number;
  };
  required_hooks: string[];
  no_repo: { app: string; project: string; repo: string }[];
  missing_hooks: { app: string; project: string; hooks_present: string[]; hooks_missing: string[] }[];
  team_mismatch: { app: string; project: string; inventory_team: string; ado_team: string }[];
  azure_pipelines: string[];
  orphans: string[];
};

export function AdoPanel() {
  const q = useQuery({ queryKey: ["gov", "ado"], queryFn: () => apiGet<Ado>("/governance/ado-coverage") });

  if (q.isLoading) return <Spinner label="Auditing repository coverage…" />;
  const d = q.data;
  if (!d) return <Empty>ADO coverage unavailable.</Empty>;
  const pct = d.headline.pct;

  return (
    <>
      <PanelHead kicker="AZURE DEVOPS — PIPELINE COVERAGE" />

      <div className="card" style={{ padding: "16px 20px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <span
            style={{
              fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 40,
              letterSpacing: "-0.03em", fontVariantNumeric: "tabular-nums", color: "var(--teal)",
            }}
          >
            {pct}%
          </span>
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
            of <strong>{d.headline.apps_total}</strong> inventory apps run the standard pipeline
            ({d.headline.pipelined} pipelined)
          </span>
        </div>
        <div
          title={`${d.headline.pipelined} pipelined / ${d.headline.apps_total - d.headline.pipelined} not pipelined`}
          style={{
            height: 14, borderRadius: 4, overflow: "hidden", display: "flex",
            background: "var(--chart-grid)", marginTop: 12,
          }}
        >
          <div style={{ width: `${pct}%`, background: "var(--ok)" }} />
          <div style={{ flex: 1, background: "var(--err)", opacity: 0.75 }} />
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 8, fontSize: 11.5, color: "var(--ink-2)" }}>
          <span><span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2, background: "var(--ok)", marginRight: 5 }} />pipelined</span>
          <span><span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2, background: "var(--err)", opacity: 0.75, marginRight: 5 }} />no pipeline</span>
        </div>
      </div>

      <Tiles>
        <Tile n={d.tiles.pipelined} label="pipelined" tone="ok" />
        <Tile n={d.tiles.no_repo} label="no pipeline" tone={d.tiles.no_repo ? "err" : undefined} />
        <Tile n={d.tiles.hooks_complete} label="hooks complete" tone="ok" />
        <Tile n={d.tiles.missing_hooks} label="missing hooks" tone={d.tiles.missing_hooks ? "warn" : undefined} />
        <Tile n={d.tiles.team_mismatch} label="team mismatch" tone={d.tiles.team_mismatch ? "warn" : undefined} />
        <Tile n={d.tiles.azure_pipelines} label="azure-pipelines" tone={d.tiles.azure_pipelines ? "warn" : undefined} />
      </Tiles>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 12.5, color: "var(--ink-2)" }}>
        <span className="card-kicker">REQUIRED SERVICE HOOKS</span>
        {d.required_hooks.map((h) => <Tag key={h} tone="teal">{h}</Tag>)}
      </div>

      <DetailsBlock title="Repos without a standard pipeline" count={d.no_repo.length} tone="err">
        {d.no_repo.length ? (
          <table className="dt">
            <thead>
              <tr><th>App</th><th>Project</th><th>Repository</th></tr>
            </thead>
            <tbody>
              {d.no_repo.map((r) => (
                <tr key={r.app}>
                  <td style={{ fontWeight: 600 }}>{r.app}</td>
                  <td>{r.project}</td>
                  <td className="mono">{r.repo}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>Every inventory app is pipelined.</Empty>
        )}
      </DetailsBlock>

      <DetailsBlock title="Pipelined repos missing required hooks" count={d.missing_hooks.length}>
        {d.missing_hooks.length ? (
          <table className="dt">
            <thead>
              <tr><th>App</th><th>Project</th><th>Hooks present</th><th>Hooks missing</th></tr>
            </thead>
            <tbody>
              {d.missing_hooks.map((r) => (
                <tr key={r.app}>
                  <td style={{ fontWeight: 600 }}>{r.app}</td>
                  <td>{r.project}</td>
                  <td>
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {r.hooks_present.map((h) => <Tag key={h} tone="ok">{h}</Tag>)}
                    </div>
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {r.hooks_missing.map((h) => <Tag key={h} tone="err">{h}</Tag>)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>All pipelined repos carry the full hook set.</Empty>
        )}
      </DetailsBlock>

      <DetailsBlock title="Inventory ↔ ADO team mismatches" count={d.team_mismatch.length}>
        {d.team_mismatch.length ? (
          <table className="dt">
            <thead>
              <tr><th>App</th><th>Project</th><th>Inventory team</th><th>ADO team</th></tr>
            </thead>
            <tbody>
              {d.team_mismatch.map((r) => (
                <tr key={r.app}>
                  <td style={{ fontWeight: 600 }}>{r.app}</td>
                  <td>{r.project}</td>
                  <td><Tag tone="ok">{r.inventory_team}</Tag></td>
                  <td><Tag tone="err">{r.ado_team}</Tag></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No team mismatches.</Empty>
        )}
      </DetailsBlock>

      <DetailsBlock title="Repos with azure-pipelines.yml (non-standard)" count={d.azure_pipelines.length}>
        {d.azure_pipelines.length ? (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "4px 0" }}>
            {d.azure_pipelines.map((a) => <Chip key={a} tone="warn"><span className="mono">{a}</span></Chip>)}
          </div>
        ) : (
          <Empty>No stray azure-pipelines definitions.</Empty>
        )}
      </DetailsBlock>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span className="card-kicker">ORPHAN REPOS (NO INVENTORY APP) · {d.orphans.length}</span>
        {d.orphans.map((o) => <Chip key={o} tone="skip"><span className="mono">{o}</span></Chip>)}
      </div>
    </>
  );
}
