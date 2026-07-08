/** Governance — index glossary and role semantics. */
import { useQuery } from "@tanstack/react-query";

import { Card, Empty, Spinner, Tag } from "../../components/ui";
import { apiGet } from "../../lib/api";

type Glossary = {
  indices: { key: string; index: string; purpose: string }[];
  roles: { role: string; sees: string; acts: string }[];
};

export function GlossaryPanel() {
  const q = useQuery({ queryKey: ["gov", "glossary"], queryFn: () => apiGet<Glossary>("/governance/glossary") });

  if (q.isLoading) return <Spinner label="Loading glossary…" />;
  const d = q.data;
  if (!d) return <Empty>Glossary unavailable.</Empty>;

  return (
    <div className="grid cols-2">
      <Card kicker="SEARCH STORE" title="Index glossary">
        <div className="table-scroll">
          <table className="dt">
            <thead>
              <tr><th>Key</th><th>Index</th><th>Purpose</th></tr>
            </thead>
            <tbody>
              {d.indices.map((i) => (
                <tr key={i.key}>
                  <td className="mono" style={{ fontWeight: 600 }}>{i.key}</td>
                  <td className="mono" style={{ color: "var(--ink-2)" }}>{i.index}</td>
                  <td style={{ fontSize: 12.5 }}>{i.purpose}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card kicker="RBAC" title="Role semantics">
        <div className="table-scroll">
          <table className="dt">
            <thead>
              <tr><th>Role</th><th>Sees</th><th>Acts</th></tr>
            </thead>
            <tbody>
              {d.roles.map((r) => (
                <tr key={r.role}>
                  <td><Tag tone={r.role === "Admin" || r.role === "CLevel" ? "gold" : ""}>{r.role}</Tag></td>
                  <td style={{ fontSize: 12.5 }}>{r.sees}</td>
                  <td style={{ fontSize: 12.5 }}>{r.acts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
