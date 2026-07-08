import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Card, Empty, Pager, Segmented, Spinner, Tag } from "../components/ui";
import { apiGet } from "../lib/api";
import { relTime } from "../lib/format";

type TypeMeta = { type: string; label: string; color: string };

type Row = {
  id: number;
  type: string;
  app: string;
  project: string;
  company: string;
  version: string;
  status: string;
  when: string;
  user: string;
  email: string;
  detail: string;
  env: string;
};

type Resp = {
  rows: Row[];
  total: number;
  page: number;
  pages: number;
  counts_by_type: Record<string, number>;
};

const WINDOWS = [
  { value: "1h", label: "1h" },
  { value: "6h", label: "6h" },
  { value: "24h", label: "24h" },
  { value: "3d", label: "3d" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "all", label: "All" },
] as const;

type Win = (typeof WINDOWS)[number]["value"];

const DOT: Record<string, string> = {
  ok: "var(--ok)",
  approved: "var(--teal)",
  failed: "var(--err)",
  running: "var(--warn)",
};

const PILL_COLOR: Record<string, string> = {
  blue: "var(--blue)",
  teal: "var(--teal)",
  gold: "var(--gold)",
  warn: "var(--warn)",
  neutral: "var(--ink-3)",
};

function tagTone(e: Row): "gold" | "teal" | "err" | "ok" | "blue" | "" {
  if (e.status === "failed") return "err";
  if (e.type.startsWith("build")) return "blue";
  if (e.type === "deploy") return "teal";
  if (e.type === "release") return "gold";
  return "";
}

function useDebounced(value: string, ms = 300): string {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function Events() {
  const [win, setWin] = useState<Win>("7d");
  const [q, setQ] = useState("");
  const [userQ, setUserQ] = useState("");
  const [off, setOff] = useState<Set<string>>(new Set()); // toggled-off types (all on by default)
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const duq = useDebounced(userQ);

  const types = useQuery({ queryKey: ["event-types"], queryFn: () => apiGet<TypeMeta[]>("/events/types") });

  const offKey = [...off].sort().join(",");
  const selTypes =
    off.size === 0 ? "" : (types.data ?? []).filter((t) => !off.has(t.type)).map((t) => t.type).join(",");

  useEffect(() => setPage(1), [win, dq, duq, offKey]);

  const events = useQuery({
    queryKey: ["events", win, selTypes, dq, duq, page],
    queryFn: () =>
      apiGet<Resp>("/events", { window: win, types: selTypes, q: dq, user: duq, page, size: 75 }),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });

  const counts = events.data?.counts_by_type ?? {};
  const labelOf: Record<string, string> = {};
  for (const t of types.data ?? []) labelOf[t.type] = t.label;

  const toggle = (t: string) =>
    setOff((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  return (
    <Card
      className="reveal"
      kicker="EVENT LOG — ROLE-SCOPED, SERVER-FILTERED"
      title="Platform events"
      actions={<span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>auto-refresh 60s</span>}
    >
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
        <Segmented options={[...WINDOWS]} value={win} onChange={setWin} />
        <input
          className="input"
          style={{ flex: 1, minWidth: 220 }}
          placeholder="Search app / project / version / detail…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <input
          className="input"
          style={{ width: 180 }}
          placeholder="Filter by user…"
          value={userQ}
          onChange={(e) => setUserQ(e.target.value)}
        />
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
        {(types.data ?? []).map((t) => {
          const on = !off.has(t.type);
          return (
            <button
              key={t.type}
              className="chip"
              onClick={() => toggle(t.type)}
              title={on ? `Hide ${t.label} events` : `Show ${t.label} events`}
              style={{ cursor: "pointer", opacity: on ? 1 : 0.45, background: on ? undefined : "transparent" }}
            >
              <span className="dot" style={{ background: on ? PILL_COLOR[t.color] ?? "var(--ink-3)" : "var(--ink-3)" }} />
              <span className="mono" style={{ fontSize: 11 }}>{t.label}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                {(counts[t.type] ?? 0).toLocaleString()}
              </span>
            </button>
          );
        })}
      </div>

      {events.isLoading ? (
        <Spinner label="Loading events…" />
      ) : events.data?.rows.length ? (
        <div style={{ opacity: events.isPlaceholderData ? 0.6 : 1, transition: "opacity .15s" }}>
          {events.data.rows.map((e) => (
            <div key={e.id} className="event-row">
              <span className="ts" title={e.when}>{relTime(e.when)}</span>
              <span className="edot" style={{ background: DOT[e.status] ?? "var(--blue)" }} />
              <span
                style={{
                  minWidth: 0,
                  flex: 1,
                  display: "flex",
                  gap: 8,
                  alignItems: "baseline",
                  overflow: "hidden",
                }}
              >
                <Tag tone={tagTone(e)}>
                  {labelOf[e.type] ?? e.type.toUpperCase()}
                  {e.type === "deploy" && e.env ? `·${e.env.toUpperCase()}` : ""}
                </Tag>
                <strong style={{ whiteSpace: "nowrap" }}>{e.app}</strong>
                <span className="mono" style={{ color: "var(--ink-2)", fontSize: 12, whiteSpace: "nowrap" }}>
                  {e.version}
                </span>
                <span
                  style={{ color: "var(--ink-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  title={e.detail}
                >
                  {e.detail}
                </span>
              </span>
              <span style={{ marginLeft: "auto", flex: "none", color: "var(--ink-3)", fontSize: 12 }} title={e.email}>
                {e.user}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <Empty>No events match the current filters in this window.</Empty>
      )}

      <Pager page={events.data?.page ?? 1} pages={events.data?.pages ?? 1} total={events.data?.total} onPage={setPage} />
    </Card>
  );
}
