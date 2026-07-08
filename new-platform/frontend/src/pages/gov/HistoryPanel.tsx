/** Governance — ES→PG history migration jobs, with 2s polling only while running. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { Chip, Empty, Spinner, Tag } from "../../components/ui";
import { apiGet, apiPost } from "../../lib/api";
import { relTime } from "../../lib/format";
import { PanelHead, Tile, Tiles } from "./common";

type Job = {
  index_key: string;
  es_index: string;
  table: string;
  total: number;
  migrated: number;
  status: string;
  mode: string;
  is_lookup: boolean;
  error: string;
  updated: string;
};

type Hist = {
  jobs: Job[];
  rollup: {
    es_total_docs: number;
    migrated_docs: number;
    pct: number;
    running: number;
    paused: number;
    done: number;
    idle: number;
  };
};

const KEY = ["gov", "history"];

export function HistoryPanel() {
  const qc = useQueryClient();
  const runningRef = useRef(false);

  const q = useQuery({
    queryKey: KEY,
    queryFn: async () => {
      const data = await apiGet<Hist>(runningRef.current ? "/governance/history/tick" : "/governance/history");
      runningRef.current = data.rollup.running > 0;
      return data;
    },
    // poll every 2s ONLY while at least one job is running
    refetchInterval: (query) =>
      ((query.state.data as Hist | undefined)?.rollup.running ?? 0) > 0 ? 2000 : false,
  });

  const act = useMutation({
    mutationFn: (v: { key: string; action: string }) =>
      apiPost<Hist>(`/governance/history/${encodeURIComponent(v.key)}/${v.action}`),
    onSuccess: (data) => {
      runningRef.current = data.rollup.running > 0;
      qc.setQueryData(KEY, data);
    },
  });

  if (q.isLoading) return <Spinner label="Loading migration jobs…" />;
  const d = q.data;
  if (!d) return <Empty>Migration state unavailable.</Empty>;

  return (
    <>
      <PanelHead kicker="HISTORICAL DOCUMENTS — SEARCH STORE → POSTGRES MIRRORS">
        {d.rollup.running > 0 && (
          <Chip tone="warn"><span className="mono">{d.rollup.running}</span> running · polling live</Chip>
        )}
      </PanelHead>

      <Tiles min={120}>
        <Tile n={d.rollup.es_total_docs} label="docs in ES" />
        <Tile n={d.rollup.migrated_docs} label="migrated" tone="ok" />
        <Tile n={`${d.rollup.pct}%`} label="coverage" tone="ok" />
        <Tile n={d.rollup.running} label="running" tone={d.rollup.running ? "blue" : undefined} />
        <Tile n={d.rollup.paused} label="paused" tone={d.rollup.paused ? "warn" : undefined} />
        <Tile n={d.rollup.done} label="done" />
        <Tile n={d.rollup.idle} label="idle" />
      </Tiles>

      <div className="grid cols-3">
        {d.jobs.map((j) => {
          const pct = j.total > 0 ? Math.min(100, (j.migrated / j.total) * 100) : 0;
          const busy = act.isPending && act.variables?.key === j.index_key;
          return (
            <div key={j.index_key} className="card" style={{ padding: "13px 15px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
                <div>
                  <div className="mono" style={{ fontWeight: 600, fontSize: 13 }}>
                    {j.index_key} {j.is_lookup && <Tag tone="blue">LOOKUP</Tag>}
                  </div>
                  <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 2 }}>
                    {j.es_index} → {j.table}
                  </div>
                </div>
                <Chip status={j.status}>{j.status.toUpperCase()}</Chip>
              </div>

              <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-2)", marginTop: 10 }}>
                {j.migrated.toLocaleString()} / {j.total.toLocaleString()} docs
              </div>
              <div style={{ height: 10, borderRadius: 4, background: "var(--chart-grid)", overflow: "hidden", marginTop: 5 }}>
                <div
                  style={{
                    height: "100%", width: `${pct}%`, minWidth: pct > 0 ? 2 : 0,
                    background: "var(--teal)", borderRadius: 4, transition: "width 0.5s ease",
                  }}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10, gap: 8 }}>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>upd {relTime(j.updated)}</span>
                <div style={{ display: "flex", gap: 6 }}>
                  {j.status === "idle" && (
                    <button className="btn sm" disabled={busy} onClick={() => act.mutate({ key: j.index_key, action: "start" })}>
                      Start
                    </button>
                  )}
                  {j.status === "running" && (
                    <button className="btn sm" disabled={busy} onClick={() => act.mutate({ key: j.index_key, action: "pause" })}>
                      Pause
                    </button>
                  )}
                  {j.status === "paused" && (
                    <button className="btn sm" disabled={busy} onClick={() => act.mutate({ key: j.index_key, action: "resume" })}>
                      Resume
                    </button>
                  )}
                  {j.status === "done" && (
                    <button className="btn sm" disabled={busy} onClick={() => act.mutate({ key: j.index_key, action: "sync_new" })}>
                      ⟳ Sync new
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
