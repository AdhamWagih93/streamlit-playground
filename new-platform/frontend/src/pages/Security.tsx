import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Card, Drawer, Empty, Kpi, Pager, Segmented, Spinner, Tag } from "../components/ui";
import { apiGet } from "../lib/api";
import { relTime } from "../lib/format";

const SEVERITIES = ["critical", "high", "medium", "low"] as const;
type Sev = (typeof SEVERITIES)[number];

const SCANNER_IDS = ["prismacloud", "invicti", "zap", "trufflehog"] as const;
type ScannerId = (typeof SCANNER_IDS)[number];
type ScannerChoice = "all" | ScannerId;

const SCANNER_LABEL: Record<ScannerId, string> = {
  prismacloud: "Prisma",
  invicti: "Invicti",
  zap: "ZAP",
  trufflehog: "TruffleHog",
};

type Cell = { critical: number; high: number; medium: number; low: number; when: string };
type Row = {
  application: string;
  project: string;
  version: string;
  env_of_version: string;
  scanners: Partial<Record<ScannerId, Cell>>;
  total_critical: number;
  total_high: number;
};
type Summary = {
  rows: Row[];
  totals: { critical: number; high: number; medium: number; low: number; apps_scanned: number; apps_total: number };
  page: number;
  pages: number;
  total: number;
};
type DetailCell = Cell & { delta: Record<Sev, number> | null };
type Detail = {
  application: string;
  project: string;
  prd_version: string | null;
  versions: { version: string; envs: string[]; scanners: Partial<Record<ScannerId, DetailCell>> }[];
};

type DrawerState =
  | { kind: "report"; title: string; scanner: ScannerId; version: string; html: string | null; error?: string }
  | { kind: "detail"; project: string; application: string }
  | null;

/** Compact per-scanner severity strip — colors come only from the .sev status classes. */
function MiniSev({ cell }: { cell?: Cell }) {
  if (!cell) return <span className="mono" style={{ color: "var(--ink-3)" }}>—</span>;
  return (
    <span style={{ display: "inline-flex", gap: 4 }}>
      {SEVERITIES.map((k) => (
        <span
          key={k}
          className={`sev ${k}`}
          title={`${k}: ${cell[k]}`}
          style={{ padding: "1px 7px", minWidth: 26, display: "inline-block" }}
        >
          <span className="n" style={{ fontSize: 12 }}>{cell[k]}</span>
        </span>
      ))}
    </span>
  );
}

function DeltaRow({ delta }: { delta: Record<Sev, number> }) {
  return (
    <span className="mono" style={{ display: "inline-flex", gap: 8, fontSize: 11, marginTop: 4 }}>
      {SEVERITIES.map((k) => {
        const v = delta[k];
        const color = v > 0 ? "var(--err)" : v < 0 ? "var(--ok)" : "var(--ink-3)";
        return (
          <span key={k} style={{ color, minWidth: 24, textAlign: "center" }} title={`Δ ${k} vs PRD`}>
            {v > 0 ? `+${v}` : v}
          </span>
        );
      })}
    </span>
  );
}

function rowWhen(r: Row): string {
  let best = "";
  for (const sc of SCANNER_IDS) {
    const w = r.scanners[sc]?.when ?? "";
    if (w > best) best = w;
  }
  return best;
}

export default function Security() {
  const [scanner, setScanner] = useState<ScannerChoice>("all");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [onlyFindings, setOnlyFindings] = useState(false);
  const [floor, setFloor] = useState<Sev>("low");
  const [page, setPage] = useState(1);
  const [drawer, setDrawer] = useState<DrawerState>(null);

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput), 300);
    return () => clearTimeout(t);
  }, [qInput]);
  useEffect(() => {
    setPage(1);
  }, [scanner, q, onlyFindings, floor]);

  const summary = useQuery({
    queryKey: ["security-summary", scanner, q, onlyFindings, floor, page],
    queryFn: () =>
      apiGet<Summary>("/security/summary", {
        scanner,
        q,
        only_findings: onlyFindings ? true : undefined,
        severity_floor: floor,
        page,
        size: 50,
      }),
  });

  const detailKey = drawer?.kind === "detail" ? drawer : null;
  const detail = useQuery({
    queryKey: ["security-app", detailKey?.project, detailKey?.application],
    queryFn: () =>
      apiGet<Detail>(
        `/security/app/${encodeURIComponent(detailKey!.project)}/${encodeURIComponent(detailKey!.application)}`,
      ),
    enabled: !!detailKey,
  });

  const openReport = async (row: Row, e: React.MouseEvent) => {
    e.stopPropagation();
    const sc: ScannerId =
      scanner !== "all" ? scanner : SCANNER_IDS.find((s) => row.scanners[s]) ?? "prismacloud";
    setDrawer({ kind: "report", title: row.application, scanner: sc, version: row.version, html: null });
    try {
      const res = await fetch(
        `/api/security/report/${sc}/${encodeURIComponent(row.project)}/${encodeURIComponent(row.application)}/${encodeURIComponent(row.version)}`,
        { credentials: "include" },
      );
      const text = await res.text();
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDrawer((d) => (d && d.kind === "report" ? { ...d, html: text } : d));
    } catch (err) {
      setDrawer((d) => (d && d.kind === "report" ? { ...d, error: String(err) } : d));
    }
  };

  const t = summary.data?.totals;
  const shownScanners: ScannerId[] = scanner === "all" ? [...SCANNER_IDS] : [scanner];

  return (
    <>
      <div className="grid cols-4 reveal">
        <Kpi
          hero
          value={t?.critical ?? 0}
          label="Critical findings — latest deployed versions"
          delta={t && t.critical > 0 ? "immediate remediation required" : "no criticals in scope"}
          deltaTone={t && t.critical > 0 ? "down" : "up"}
        />
        <Kpi value={t?.high ?? 0} label="High findings" delta={`${t?.medium ?? 0} medium · ${t?.low ?? 0} low`} />
        <Kpi
          value={t?.apps_scanned ?? 0}
          suffix={` / ${t?.apps_total ?? 0}`}
          label="Apps scanned"
          delta="coverage of visible fleet"
        />
        <Kpi value={4} label="Scanners active" delta="prisma · invicti · zap · trufflehog" />
      </div>

      <Card className="reveal" kicker="SECURITY POSTURE — LATEST DEPLOYED VERSION PER APP" title="Fleet scan matrix">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 14 }}>
          <Segmented<ScannerChoice>
            value={scanner}
            onChange={setScanner}
            options={[
              { value: "all", label: "All" },
              { value: "prismacloud", label: "Prisma" },
              { value: "invicti", label: "Invicti" },
              { value: "zap", label: "ZAP" },
              { value: "trufflehog", label: "TruffleHog" },
            ]}
          />
          <input
            className="input"
            placeholder="Search app or project…"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            style={{ minWidth: 220 }}
          />
          <button
            className="btn sm"
            aria-pressed={onlyFindings}
            onClick={() => setOnlyFindings((v) => !v)}
            style={
              onlyFindings
                ? { borderColor: "rgba(58,198,180,.4)", color: "var(--teal)", background: "rgba(58,198,180,.08)" }
                : undefined
            }
          >
            {onlyFindings ? "◉" : "○"} only apps with findings
          </button>
          <select className="input" value={floor} onChange={(e) => setFloor(e.target.value as Sev)}>
            <option value="low">All severities</option>
            <option value="medium">Medium and above</option>
            <option value="high">High and above</option>
            <option value="critical">Critical only</option>
          </select>
        </div>

        {summary.isLoading ? (
          <Spinner label="Aggregating scan results…" />
        ) : summary.isError ? (
          <Empty>Could not load security summary.</Empty>
        ) : !summary.data?.rows.length ? (
          <Empty>No applications match the current filters.</Empty>
        ) : (
          <>
            <div className="table-scroll">
              <table className="dt">
                <thead>
                  <tr>
                    <th>App</th>
                    <th>Version</th>
                    {shownScanners.map((s) => (
                      <th key={s}>{SCANNER_LABEL[s]} · C/H/M/L</th>
                    ))}
                    <th>When</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {summary.data.rows.map((r) => (
                    <tr
                      key={`${r.project}/${r.application}`}
                      style={{ cursor: "pointer" }}
                      onClick={() => setDrawer({ kind: "detail", project: r.project, application: r.application })}
                    >
                      <td>
                        <strong>{r.application}</strong>
                        <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{r.project}</div>
                      </td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <Tag>{r.version}</Tag>{" "}
                        {r.env_of_version && (
                          <Tag tone={r.env_of_version === "prd" ? "teal" : ""}>{r.env_of_version.toUpperCase()}</Tag>
                        )}
                      </td>
                      {shownScanners.map((s) => (
                        <td key={s} style={{ whiteSpace: "nowrap" }}>
                          <MiniSev cell={r.scanners[s]} />
                        </td>
                      ))}
                      <td className="mono" style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>
                        {relTime(rowWhen(r)) }
                      </td>
                      <td>
                        <button className="btn sm" onClick={(e) => openReport(r, e)}>
                          Report ▸
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={summary.data.page} pages={summary.data.pages} total={summary.data.total} onPage={setPage} />
          </>
        )}
      </Card>

      <Drawer open={!!drawer} onClose={() => setDrawer(null)}>
        {drawer?.kind === "report" && (
          <>
            <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div>
                <div className="card-kicker">SCAN REPORT — {SCANNER_LABEL[drawer.scanner].toUpperCase()}</div>
                <div className="card-title" style={{ marginBottom: 0 }}>
                  {drawer.title} <Tag>{drawer.version}</Tag>
                </div>
              </div>
              <button className="btn sm ghost" onClick={() => setDrawer(null)}>✕ Close</button>
            </header>
            {drawer.error ? (
              <Empty>Report unavailable: {drawer.error}</Empty>
            ) : drawer.html === null ? (
              <Spinner label="Fetching report…" />
            ) : (
              <iframe
                sandbox=""
                srcDoc={drawer.html}
                title={`${drawer.scanner} report — ${drawer.title} ${drawer.version}`}
                style={{
                  width: "100%",
                  height: "calc(100vh - 118px)",
                  border: "1px solid var(--stroke)",
                  borderRadius: 10,
                  background: "#0B1020",
                }}
              />
            )}
          </>
        )}

        {drawer?.kind === "detail" && (
          <>
            <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <div>
                <div className="card-kicker">VERSION DRILL-DOWN — Δ VS PRD</div>
                <div className="card-title" style={{ marginBottom: 0 }}>
                  {drawer.application}
                  {detail.data?.prd_version && (
                    <>
                      {" "}
                      <Tag tone="teal">PRD {detail.data.prd_version}</Tag>
                    </>
                  )}
                </div>
              </div>
              <button className="btn sm ghost" onClick={() => setDrawer(null)}>✕ Close</button>
            </header>
            {detail.isLoading ? (
              <Spinner label="Loading version history…" />
            ) : detail.isError ? (
              <Empty>Could not load drill-down for this application.</Empty>
            ) : !detail.data?.versions.length ? (
              <Empty>No scanned versions found for this application.</Empty>
            ) : (
              <div className="table-scroll">
                <table className="dt">
                  <thead>
                    <tr>
                      <th>Version</th>
                      {SCANNER_IDS.map((s) => (
                        <th key={s}>{SCANNER_LABEL[s]}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {detail.data.versions.map((v) => (
                      <tr key={v.version}>
                        <td style={{ whiteSpace: "nowrap" }}>
                          <Tag tone={v.version === detail.data!.prd_version ? "teal" : ""}>{v.version}</Tag>
                          <div style={{ display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap" }}>
                            {v.envs.map((e) => (
                              <span key={e} className="mono" style={{ fontSize: 10, color: "var(--ink-3)" }}>
                                {e.toUpperCase()}
                              </span>
                            ))}
                          </div>
                        </td>
                        {SCANNER_IDS.map((s) => {
                          const c = v.scanners[s];
                          return (
                            <td key={s} style={{ whiteSpace: "nowrap" }}>
                              {c ? (
                                <div style={{ display: "flex", flexDirection: "column" }}>
                                  <MiniSev cell={c} />
                                  {c.delta && <DeltaRow delta={c.delta} />}
                                </div>
                              ) : (
                                <span className="mono" style={{ color: "var(--ink-3)" }}>—</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 10 }}>
                  Δ row under each scanner = change vs the PRD version (red worse · green better).
                </div>
              </div>
            )}
          </>
        )}
      </Drawer>
    </>
  );
}
