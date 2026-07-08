/** Small shared pieces for the governance sub-panels. */
import type { ReactNode } from "react";

/** Compact stat tile (sev-style box, neutral by default). */
export function Tile(props: { n: number | string; label: string; tone?: "ok" | "warn" | "err" | "blue" }) {
  const color =
    props.tone === "err" ? "var(--err)"
      : props.tone === "warn" ? "var(--warn)"
      : props.tone === "ok" ? "var(--teal)"
      : props.tone === "blue" ? "var(--blue)"
      : "var(--ink)";
  return (
    <div className="sev">
      <div className="n" style={{ color }}>{typeof props.n === "number" ? props.n.toLocaleString() : props.n}</div>
      <div className="l">{props.label}</div>
    </div>
  );
}

export function Tiles(props: { children: ReactNode; min?: number }) {
  return (
    <div className="grid" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${props.min ?? 130}px, 1fr))`, gap: 8 }}>
      {props.children}
    </div>
  );
}

/** Collapsible warning block with a capped, scrollable body. */
export function DetailsBlock(props: { title: string; count: number; tone?: "warn" | "err"; children: ReactNode }) {
  const countColor = props.count === 0 ? "var(--ink-3)" : props.tone === "err" ? "var(--err)" : "var(--warn)";
  return (
    <details className="card" style={{ padding: "12px 16px" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 13 }}>
        {props.title}{" "}
        <span className="mono" style={{ fontSize: 11.5, color: countColor }}>({props.count})</span>
      </summary>
      <div className="table-scroll" style={{ maxHeight: 280, overflowY: "auto", marginTop: 10 }}>
        {props.children}
      </div>
    </details>
  );
}

/** Header row for a panel: kicker text left, actions right. */
export function PanelHead(props: { kicker: string; children?: ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
      <div className="card-kicker">{props.kicker}</div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>{props.children}</div>
    </div>
  );
}

export function CleanBanner(props: { children: ReactNode }) {
  return (
    <div
      className="card"
      style={{
        borderColor: "rgba(61, 214, 140, 0.35)",
        background: "rgba(61, 214, 140, 0.05)",
        color: "var(--ok)",
        fontWeight: 600,
        fontSize: 13,
        padding: "14px 18px",
      }}
    >
      ✓ {props.children}
    </div>
  );
}
