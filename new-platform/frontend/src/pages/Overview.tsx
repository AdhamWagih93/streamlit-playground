import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Card, Chip, Empty, Kpi, Spinner, Tag } from "../components/ui";
import { apiGet } from "../lib/api";
import { useAuth } from "../lib/auth";
import { relTime } from "../lib/format";

type Summary = {
  applications: number;
  pipelines: number;
  teams: number;
  environments: number;
  live_in_prd: number;
  failed_deploys_24h: number;
  open_incidents: number;
  projects: number;
  companies: number;
};

type Ev = {
  id: number;
  type: string;
  app: string;
  project: string;
  version: string;
  status: string;
  when: string;
  user: string;
  detail: string;
  env: string;
};

type Integration = { key: string; label: string; glyph: string; state: string; detail: string; tip: string };

const EV_COLOR: Record<string, string> = {
  ok: "var(--ok)",
  approved: "var(--ok)",
  failed: "var(--err)",
  running: "var(--warn)",
};

const AI_SERVICES = [
  { to: "/incidents", glyph: "✦", title: "Incident analysis", desc: "One-click root cause across rollout logs, events, configs and release history." },
  { to: "/assistant", glyph: "✧", title: "Knowledge assistant", desc: "Doc-grounded answers with citations, for every engineering persona." },
  { to: "/architecture", glyph: "⌬", title: "Architecture discovery", desc: "Reconstruct topology from source and flag standard violations." },
];

export default function Overview() {
  const { me } = useAuth();
  const summary = useQuery({ queryKey: ["overview"], queryFn: () => apiGet<Summary>("/overview/summary") });
  const events = useQuery({
    queryKey: ["overview-events"],
    queryFn: () => apiGet<Ev[]>("/overview/events", { limit: 12 }),
    refetchInterval: 5000,
  });
  const integrations = useQuery({
    queryKey: ["integrations"],
    queryFn: () => apiGet<Integration[]>("/meta/integrations"),
    enabled: !!me?.is_admin,
    refetchInterval: 90_000,
  });

  if (summary.isLoading) return <Spinner label="Loading platform overview…" />;
  const s = summary.data;

  return (
    <>
      <div className="grid cols-4 reveal">
        <Kpi hero value={s?.applications ?? 0} label="Applications under governance" delta={`${s?.projects ?? 0} projects · ${s?.companies ?? 0} companies`} />
        <Kpi value={s?.pipelines ?? 0} label="Registered pipelines" delta={s ? `${Math.round((s.pipelines / Math.max(1, s.applications)) * 100)}% of fleet standardised` : ""} deltaTone="up" />
        <Kpi value={s?.teams ?? 0} label="Engineering teams" delta="RBAC-scoped" />
        <Kpi value={s?.environments ?? 0} label="Governed environments" delta="DEV → QA → UAT → PROD" />
      </div>

      <div className="grid cols-3 reveal">
        <Card className="span-2" kicker="DELIVERY FLEET" title={
          <>
            <span className="live-dot" /> Live platform events
          </>
        } actions={<Link className="btn sm ghost" to="/events">Full log ▸</Link>}>
          {events.data?.length ? (
            <div>
              {events.data.map((e) => (
                <div key={e.id} className="event-row">
                  <span className="ts">{relTime(e.when)}</span>
                  <span className="edot" style={{ background: EV_COLOR[e.status] ?? "var(--blue)" }} />
                  <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    <Tag tone={e.status === "failed" ? "err" : e.type.startsWith("build") ? "blue" : e.type === "deploy" ? "teal" : ""}>
                      {e.type.toUpperCase()}
                      {e.env ? `·${e.env.toUpperCase()}` : ""}
                    </Tag>{" "}
                    <strong>{e.app}</strong> <span className="mono" style={{ color: "var(--ink-2)" }}>{e.version}</span>{" "}
                    <span style={{ color: "var(--ink-2)" }}>{e.detail}</span>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty>No events in scope.</Empty>
          )}
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <Card gold kicker="AI — EMBEDDED, NOT BOLTED ON" title="Intelligence services">
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {AI_SERVICES.filter((x) => x.to !== "/architecture" || me?.is_admin).map((x) => (
                <Link key={x.to} to={x.to} className="card" style={{ padding: "10px 14px", display: "flex", gap: 12, alignItems: "start" }}>
                  <span className="ai-glyph" style={{ fontSize: 18 }}>{x.glyph}</span>
                  <span>
                    <div style={{ fontWeight: 600, color: "var(--ink)" }}>{x.title}</div>
                    <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{x.desc}</div>
                  </span>
                </Link>
              ))}
            </div>
          </Card>
          <Card kicker="POSTURE" title="Right now">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <Chip tone="ok">{s?.live_in_prd ?? 0} live in PROD</Chip>
              <Chip tone={s?.failed_deploys_24h ? "err" : "ok"}>{s?.failed_deploys_24h ?? 0} failed deploys · 24h</Chip>
              <Chip tone={s?.open_incidents ? "warn" : "ok"}>{s?.open_incidents ?? 0} open incidents</Chip>
            </div>
          </Card>
        </div>
      </div>

      {me?.is_admin && (
        <Card className="reveal" kicker="PLATFORM INTEGRATIONS — DESCRIBED BY ROLE, NEVER BY TOOL" title="Integration health">
          {integrations.data ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {integrations.data.map((i) => (
                <Chip key={i.key} tone={(i.state as "ok" | "warn" | "err") ?? "skip"} title={i.tip}>
                  {i.glyph} {i.label} <span className="mono" style={{ color: "var(--ink-3)" }}>{i.detail}</span>
                </Chip>
              ))}
            </div>
          ) : (
            <Spinner />
          )}
        </Card>
      )}
    </>
  );
}
