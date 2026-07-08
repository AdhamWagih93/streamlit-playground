/** Dependency-free layered SVG topology. Services grouped by project in left
 *  columns; data stores / queues / directory / externals in right columns.
 *  Sync edges: solid blue. Async (kafka/amqp): dashed teal. Hover highlights
 *  a node's edges; click opens the provenance panel (via onSelect). */
import { useMemo, useState } from "react";

import type { ArchModel, ArchNode } from "./types";

type Pos = { x: number; y: number };
type Section = { title: string; items: ArchNode[] };
type Column = { sections: Section[] };

const GLYPH: Record<string, string> = { db: "▤", queue: "▮▮", ldap: "◎", external: "◇" };

function truncate(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export default function Topology(props: {
  model: ArchModel;
  compact?: boolean;
  idPrefix: string;
  selectedId?: string | null;
  onSelect?: (n: ArchNode | null) => void;
}) {
  const { nodes, edges } = props.model;
  const compact = !!props.compact;
  const W = compact ? 150 : 186;
  const H = compact ? 38 : 48;
  const COL_GAP = compact ? 80 : 118;
  const ROW_GAP = compact ? 11 : 15;
  const PAD = 22;
  const HEADER = compact ? 20 : 26;
  const [hover, setHover] = useState<string | null>(null);

  const layout = useMemo(() => {
    const services = nodes.filter((n) => n.type === "service");
    const infra = nodes
      .filter((n) => n.type !== "service")
      .sort((a, b) => a.type.localeCompare(b.type) || a.id.localeCompare(b.id));

    const byProject = new Map<string, ArchNode[]>();
    for (const n of services) {
      const k = n.project || "—";
      byProject.set(k, [...(byProject.get(k) ?? []), n]);
    }
    const groups: Section[] = [...byProject.entries()]
      .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
      .map(([title, items]) => ({ title, items }));

    // Greedy bin-packing of project groups into service columns.
    const nCols = Math.max(1, Math.ceil(services.length / (compact ? 10 : 13)));
    const unitsTotal = services.length + groups.length * 0.7;
    const budget = unitsTotal / nCols;
    const columns: Column[] = [];
    let cur: Section[] = [];
    let used = 0;
    for (const g of groups) {
      const cost = g.items.length + 0.7;
      if (cur.length && used + cost > budget * 1.2) {
        columns.push({ sections: cur });
        cur = [];
        used = 0;
      }
      cur.push(g);
      used += cost;
    }
    if (cur.length) columns.push({ sections: cur });

    // Infra split into right-hand columns.
    if (infra.length) {
      const maxRows = Math.max(8, Math.ceil(budget) + 2);
      const nInfra = Math.max(1, Math.ceil(infra.length / maxRows));
      const per = Math.ceil(infra.length / nInfra);
      for (let i = 0; i < nInfra; i++) {
        columns.push({
          sections: [{ title: i === 0 ? "DATA & DIRECTORY" : "· · ·", items: infra.slice(i * per, (i + 1) * per) }],
        });
      }
    }

    const pos = new Map<string, Pos>();
    const order = new Map<string, number>();
    let idx = 0;
    let height = 0;
    columns.forEach((col, ci) => {
      const x = PAD + ci * (W + COL_GAP);
      let y = PAD;
      for (const sec of col.sections) {
        y += HEADER;
        for (const n of sec.items) {
          pos.set(n.id, { x, y });
          order.set(n.id, idx++);
          y += H + ROW_GAP;
        }
        y += 10; // gap between project groups
      }
      height = Math.max(height, y);
    });
    const width = PAD * 2 + columns.length * W + (columns.length - 1) * COL_GAP;
    return { columns, pos, order, width, height: height + PAD };
  }, [nodes, compact, W, H, COL_GAP, ROW_GAP, HEADER]);

  const pid = props.idPrefix;
  const touched = (src: string, tgt: string) => hover === src || hover === tgt;

  return (
    <div style={{ overflowX: "auto" }}>
      <svg
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        style={{ display: "block", minWidth: Math.min(layout.width, 600) }}
        role="img"
        aria-label="Architecture topology"
      >
        <defs>
          <marker id={`${pid}-arr-sync`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--blue)" />
          </marker>
          <marker id={`${pid}-arr-async`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--teal)" />
          </marker>
        </defs>

        <rect x={0} y={0} width={layout.width} height={layout.height} fill="transparent" onClick={() => props.onSelect?.(null)} />

        {/* column / project headers */}
        {layout.columns.map((col, ci) => {
          const x = PAD + ci * (W + COL_GAP);
          let y = PAD;
          return col.sections.map((sec, si) => {
            const hy = y + HEADER - 8;
            y += HEADER + sec.items.length * (H + ROW_GAP) + 10;
            return (
              <text
                key={`${ci}-${si}`}
                x={x}
                y={hy}
                fontSize={compact ? 8.5 : 9.5}
                fill="var(--ink-3)"
                fontFamily="var(--font-mono)"
                letterSpacing="0.12em"
                style={{ textTransform: "uppercase" }}
              >
                {sec.title.toUpperCase()}
              </text>
            );
          });
        })}

        {/* edges */}
        {edges.map((e, i) => {
          const s = layout.pos.get(e.source);
          const t = layout.pos.get(e.target);
          if (!s || !t) return null;
          const x1 = s.x + W;
          const y1 = s.y + H / 2;
          const x2 = t.x - 5;
          const y2 = t.y + H / 2;
          const dx = Math.max(46, Math.abs(x2 - x1) * 0.45);
          const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
          const hot = touched(e.source, e.target);
          const opacity = hover ? (hot ? 0.95 : 0.1) : e.async ? 0.6 : 0.5;
          return (
            <path
              key={i}
              d={d}
              fill="none"
              stroke={e.async ? "var(--teal)" : "var(--blue)"}
              strokeWidth={hot ? 2 : 1.3}
              strokeDasharray={e.async ? "6 5" : undefined}
              opacity={opacity}
              markerEnd={`url(#${pid}-arr-${e.async ? "async" : "sync"})`}
              style={{ transition: "opacity .15s, stroke-width .15s", pointerEvents: "none" }}
            >
              <title>{`${e.source} → ${e.target} (${e.scheme})`}</title>
            </path>
          );
        })}

        {/* nodes */}
        {nodes.map((n) => {
          const p = layout.pos.get(n.id);
          if (!p) return null;
          const i = layout.order.get(n.id) ?? 0;
          const selected = props.selectedId === n.id;
          const stroke = selected ? "var(--gold)" : n.is_legacy ? "var(--warn)" : n.type === "external" ? "var(--stroke)" : "var(--stroke-strong)";
          const dash = n.is_legacy ? "6 4" : n.type === "external" ? "3 3" : undefined;
          const fill = n.type === "service" ? "var(--surface-2)" : "var(--surface-1)";
          const version = n.provenance?.deployed_version || "";
          const glyph = GLYPH[n.type];
          const label = `${glyph ? `${glyph} ` : ""}${truncate(n.label, compact ? 17 : 21)}`;
          const r = 12;
          return (
            <g
              key={n.id}
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover(null)}
              onClick={() => props.onSelect?.(n)}
              style={{
                cursor: props.onSelect ? "pointer" : "default",
                animation: "slidein .4s var(--ease) both",
                animationDelay: `${Math.min(i, 48) * 22}ms`,
              }}
            >
              {n.type === "db" ? (
                <path
                  d={`M ${p.x} ${p.y + r} Q ${p.x} ${p.y} ${p.x + r} ${p.y} L ${p.x + W - r} ${p.y} Q ${p.x + W} ${p.y} ${p.x + W} ${p.y + r} L ${p.x + W} ${p.y + H} L ${p.x} ${p.y + H} Z`}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={selected ? 2 : 1.2}
                  strokeDasharray={dash}
                />
              ) : (
                <rect
                  x={p.x}
                  y={p.y}
                  width={W}
                  height={H}
                  rx={n.type === "ldap" ? H / 2 : n.type === "queue" ? 4 : 10}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={selected ? 2 : 1.2}
                  strokeDasharray={dash}
                />
              )}
              <text
                x={p.x + (n.type === "ldap" ? 16 : 11)}
                y={version ? p.y + H / 2 - 2 : p.y + H / 2 + 4}
                fontSize={compact ? 10 : 11.5}
                fontWeight={600}
                fill={n.is_legacy ? "var(--warn)" : "var(--ink)"}
                style={{ pointerEvents: "none" }}
              >
                {label}
              </text>
              {version && (
                <text
                  x={p.x + (n.type === "ldap" ? 16 : 11)}
                  y={p.y + H / 2 + (compact ? 11 : 13)}
                  fontSize={compact ? 8.5 : 9.5}
                  fill="var(--ink-3)"
                  fontFamily="var(--font-mono)"
                  style={{ pointerEvents: "none" }}
                >
                  {version}
                </text>
              )}
              <title>{`${n.label}${n.project ? ` · ${n.project}` : ""} (${n.type}${n.is_legacy ? " · legacy" : ""})`}</title>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
