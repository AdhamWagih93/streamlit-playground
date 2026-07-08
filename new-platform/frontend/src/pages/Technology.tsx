import { useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";

import { Card, Empty, HBar, Kpi, Segmented, Spinner, Tag } from "../components/ui";
import { apiGet } from "../lib/api";

type Dim = "build_technology" | "deploy_technology" | "deploy_platform";
type By = "team" | "project";

type Ranked = { value: string; count: number; pct: number };

type TechSummary = {
  dim: Dim;
  by: By;
  kpis: {
    build_technology: number;
    deploy_technology: number;
    deploy_platform: number;
    apps_total: number;
    fully_specified_pct: number;
  };
  ranked: Ranked[];
  unset: number;
  most: Ranked | null;
  least: Ranked | null;
  matrix: { cols: string[]; rows: { value: string; cells: number[]; total: number }[]; col_totals: number[] };
  consolidation: { unset_apps: string[]; singletons: { value: string; app: string }[] };
};

const DIM_LABEL: Record<Dim, string> = {
  build_technology: "Build technology",
  deploy_technology: "Deploy technology",
  deploy_platform: "Deploy platform",
};

const WARN_CARD: CSSProperties = {
  borderColor: "rgba(242, 177, 76, 0.45)",
  background: "linear-gradient(180deg, rgba(242, 177, 76, 0.05), var(--surface-1))",
};

function HighlightCard(props: { kicker: string; item: Ranked | null; singleton?: boolean }) {
  return (
    <Card kicker={props.kicker} title="">
      {!props.item ? (
        <Empty>No data.</Empty>
      ) : (
        <>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 30, letterSpacing: "-0.02em", lineHeight: 1.1 }}>
            {props.item.value}
          </div>
          <div className="mono" style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 6 }}>
            {props.item.count} app{props.item.count === 1 ? "" : "s"} · {props.item.pct}% of fleet
          </div>
          {props.singleton && props.item.count === 1 && (
            <div style={{ marginTop: 8 }}>
              <span className="tag" style={{ color: "var(--warn)", borderColor: "rgba(242,177,76,.35)", background: "rgba(242,177,76,.07)" }}>
                single-app technology — consolidation candidate
              </span>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

export default function Technology() {
  const [dim, setDim] = useState<Dim>("build_technology");
  const [by, setBy] = useState<By>("team");

  const q = useQuery({
    queryKey: ["tech-summary", dim, by],
    queryFn: () => apiGet<TechSummary>("/technology/summary", { dim, by }),
  });

  if (q.isLoading) return <Spinner label="Loading technology analytics…" />;
  const d = q.data;
  if (!d) return <Empty>No technology data in scope.</Empty>;

  const maxCount = d.ranked[0]?.count ?? 1;
  const barMax = Math.max(maxCount, d.unset);
  const colMax = d.matrix.cols.map((_, j) => Math.max(1, ...d.matrix.rows.map((r) => r.cells[j])));

  return (
    <>
      <div className="grid cols-4 reveal">
        <Kpi value={d.kpis.build_technology} label="Build technologies in use" delta={`${d.kpis.apps_total} apps in scope`} />
        <Kpi value={d.kpis.deploy_technology} label="Deploy technologies in use" />
        <Kpi value={d.kpis.deploy_platform} label="Deploy platforms in use" />
        <Kpi
          value={d.kpis.fully_specified_pct}
          suffix="%"
          label="Fully specified apps"
          delta="build + deploy + platform all set"
          deltaTone={d.kpis.fully_specified_pct >= 95 ? "up" : "flat"}
        />
      </div>

      <Card className="reveal">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
          <Segmented<Dim>
            options={[
              { value: "build_technology", label: "Build tech" },
              { value: "deploy_technology", label: "Deploy tech" },
              { value: "deploy_platform", label: "Platform" },
            ]}
            value={dim}
            onChange={setDim}
          />
          <Segmented<By>
            options={[
              { value: "team", label: "By team" },
              { value: "project", label: "By project" },
            ]}
            value={by}
            onChange={setBy}
          />
          <span className="mono" style={{ marginLeft: "auto", fontSize: 12, color: "var(--ink-3)" }}>
            {DIM_LABEL[dim]} · {d.ranked.length} distinct · {d.unset} unset
          </span>
        </div>
      </Card>

      <div className="grid cols-3 reveal">
        <Card className="span-2" kicker="RANKED USAGE" title={`${DIM_LABEL[dim]} across the fleet`}>
          {d.ranked.length === 0 ? (
            <Empty>No values recorded for this dimension.</Empty>
          ) : (
            <div>
              {d.ranked.map((r) => (
                <HBar key={r.value} label={r.value} value={r.count} max={barMax} color="var(--chart-1)" />
              ))}
              {d.unset > 0 && <HBar label="(unset)" value={d.unset} max={barMax} color="rgba(107, 118, 153, 0.45)" />}
            </div>
          )}
        </Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <HighlightCard kicker="MOST USED" item={d.most} />
          <HighlightCard kicker="LEAST USED" item={d.least} singleton />
        </div>
      </div>

      <Card className="reveal" kicker="CROSS-REFERENCE" title={`${DIM_LABEL[dim]} × ${by === "team" ? "dev team" : "project"}`}>
        {d.matrix.rows.length === 0 ? (
          <Empty>Nothing to cross-reference.</Empty>
        ) : (
          <div className="table-scroll">
            <table className="dt">
              <thead>
                <tr>
                  <th>{DIM_LABEL[dim]}</th>
                  {d.matrix.cols.map((c) => (
                    <th key={c} style={{ textAlign: "center" }}>{c}</th>
                  ))}
                  <th style={{ textAlign: "right" }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {d.matrix.rows.map((r) => (
                  <tr key={r.value}>
                    <td style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{r.value}</td>
                    {r.cells.map((v, j) => (
                      <td
                        key={j}
                        className="num"
                        style={{
                          textAlign: "center",
                          background: v > 0 ? `rgba(58, 198, 180, ${(0.08 + 0.6 * (v / colMax[j])).toFixed(3)})` : undefined,
                        }}
                      >
                        {v > 0 ? v : "·"}
                      </td>
                    ))}
                    <td className="num" style={{ textAlign: "right", fontWeight: 600 }}>{r.total}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td className="mono" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.12em", color: "var(--ink-3)" }}>
                    Total
                  </td>
                  {d.matrix.col_totals.map((t, j) => (
                    <td key={j} className="num" style={{ textAlign: "center", color: "var(--ink-2)", borderTop: "1px solid var(--stroke)" }}>
                      {t}
                    </td>
                  ))}
                  <td className="num" style={{ textAlign: "right", color: "var(--ink-2)", borderTop: "1px solid var(--stroke)" }}>
                    {d.matrix.col_totals.reduce((a, b) => a + b, 0)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>

      {(d.consolidation.unset_apps.length > 0 || d.consolidation.singletons.length > 0) && (
        <Card className="reveal" style={WARN_CARD} kicker="CONSOLIDATION" title={<span style={{ color: "var(--warn)" }}>Standardisation opportunities</span>}>
          {d.consolidation.unset_apps.length > 0 && (
            <div style={{ marginBottom: d.consolidation.singletons.length ? 14 : 0 }}>
              <div style={{ fontSize: 13, color: "var(--ink-2)", marginBottom: 8 }}>
                {d.unset} app{d.unset === 1 ? " has" : "s have"} no {DIM_LABEL[dim].toLowerCase()} recorded
                {d.unset > d.consolidation.unset_apps.length ? ` (showing ${d.consolidation.unset_apps.length})` : ""}:
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {d.consolidation.unset_apps.map((a) => (
                  <Tag key={a}>{a}</Tag>
                ))}
              </div>
            </div>
          )}
          {d.consolidation.singletons.length > 0 && (
            <div>
              <div style={{ fontSize: 13, color: "var(--ink-2)", marginBottom: 8 }}>
                Technologies carried by a single application:
              </div>
              {d.consolidation.singletons.map((s) => (
                <div key={s.value} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: 13 }}>
                  <span className="tag" style={{ color: "var(--warn)", borderColor: "rgba(242,177,76,.35)", background: "rgba(242,177,76,.07)" }}>
                    {s.value}
                  </span>
                  <span style={{ color: "var(--ink-3)" }}>only used by</span>
                  <strong>{s.app}</strong>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </>
  );
}
