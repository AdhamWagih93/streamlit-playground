/** Per-page integration requirements strip.
 *
 * Mounted once in the Shell; looks up the features hosted on the current route and
 * shows, for each, whether its integrations are satisfied — and exactly which missing
 * integration blocks which feature. In demo mode features are served from demo data,
 * so the strip shows what live mode will need instead of blocking.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";

import { apiGet } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Tag } from "./ui";

type Requirements = {
  data_mode: "demo" | "live";
  integrations: Record<string, { role: string; tool: string; glyph: string }>;
  state: Record<string, boolean>;
  features: Array<{
    key: string;
    label: string;
    route: string;
    requires: string[];
    optional: string[];
    missing: string[];
    missing_optional: string[];
    available: boolean;
  }>;
};

export function IntegrationStrip() {
  const { pathname } = useLocation();
  const { me } = useAuth();
  const q = useQuery({
    queryKey: ["requirements"],
    queryFn: () => apiGet<Requirements>("/settings/requirements"),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
  if (!q.data || pathname === "/settings") return null;
  const d = q.data;
  const here = d.features.filter((f) => f.route === pathname);
  if (!here.length) return null;

  const name = (k: string) => d.integrations[k] ?? { role: k, tool: k, glyph: "◌" };
  const anyMissing = here.some((f) => f.missing.length > 0);
  const demo = d.data_mode === "demo";

  return (
    <div
      className="card"
      style={{
        padding: "8px 14px",
        display: "flex",
        alignItems: "center",
        gap: 10,
        flexWrap: "wrap",
        borderColor: !demo && anyMissing ? "rgba(240,106,106,.4)" : undefined,
      }}
    >
      <span className="card-kicker" style={{ whiteSpace: "nowrap" }}>
        {demo ? "INTEGRATIONS · DEMO-SERVED" : "INTEGRATIONS"}
      </span>
      {here.map((f) => (
        <span key={f.key} style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {here.length > 1 && (
            <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{f.label}:</span>
          )}
          {f.requires.map((k) => {
            const ok = d.state[k];
            const n = name(k);
            return (
              <Tag
                key={k}
                tone={ok ? "teal" : demo ? "" : "err"}
                title={
                  ok
                    ? `${n.role} (${n.tool}) — configured`
                    : demo
                      ? `${n.role} (${n.tool}) — served from demo data here; live mode needs it for “${f.label}”`
                      : `${n.role} (${n.tool}) — NOT configured; “${f.label}” will not work in live mode`
                }
              >
                {n.glyph} {n.role} {ok ? "✓" : demo ? "· demo" : "✗ missing"}
              </Tag>
            );
          })}
          {f.missing_optional.map((k) => {
            const n = name(k);
            return (
              <Tag key={k} title={`${n.role} (${n.tool}) — optional: enhances “${f.label}”`}>
                {n.glyph} {n.role} · optional
              </Tag>
            );
          })}
        </span>
      ))}
      {(anyMissing || here.some((f) => f.missing_optional.length > 0)) && me?.is_admin && (
        <Link to="/settings" className="btn sm ghost" style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
          ⚙ Configure in Settings ▸
        </Link>
      )}
      {anyMissing && !me?.is_admin && !demo && (
        <span style={{ marginLeft: "auto", fontSize: 11.5, color: "var(--ink-3)" }}>
          ask a platform admin to configure
        </span>
      )}
    </div>
  );
}
