/** MERIDIAN UI primitives. Keep these tiny and composable — pages assemble them. */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { STATUS_TONE } from "../lib/format";

export function Card(props: {
  title?: ReactNode;
  kicker?: string;
  gold?: boolean;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <section className={`card ${props.gold ? "gold" : ""} ${props.className ?? ""}`} style={props.style}>
      {(props.title || props.kicker || props.actions) && (
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 12 }}>
          <div>
            {props.kicker && <div className="card-kicker">{props.kicker}</div>}
            {props.title && <div className="card-title">{props.title}</div>}
          </div>
          {props.actions && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{props.actions}</div>}
        </header>
      )}
      {props.children}
    </section>
  );
}

/** Count-up KPI number (tabular-nums, eased). */
export function Kpi(props: { value: number; label: string; delta?: string; deltaTone?: "up" | "down" | "flat"; hero?: boolean; suffix?: string }) {
  const [shown, setShown] = useState(0);
  const target = props.value;
  const raf = useRef(0);
  useEffect(() => {
    const t0 = performance.now();
    const dur = 900;
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / dur);
      setShown(Math.round(target * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target]);
  return (
    <div className={`kpi ${props.hero ? "hero" : ""}`}>
      <div className="kpi-num">
        {shown.toLocaleString()}
        {props.suffix ?? ""}
      </div>
      <div className="kpi-label">{props.label}</div>
      {props.delta && <div className={`kpi-delta ${props.deltaTone ?? "flat"}`}>{props.delta}</div>}
    </div>
  );
}

export function Chip(props: { tone?: "ok" | "warn" | "err" | "skip"; status?: string; children: ReactNode; title?: string }) {
  const tone = props.tone ?? STATUS_TONE[(props.status ?? "").toLowerCase()] ?? "skip";
  return (
    <span className={`chip ${tone}`} title={props.title}>
      <span className="dot" />
      {props.children}
    </span>
  );
}

export function Tag(props: { tone?: "gold" | "teal" | "err" | "ok" | "blue" | ""; children: ReactNode; title?: string }) {
  return (
    <span className={`tag ${props.tone ?? ""}`} title={props.title}>
      {props.children}
    </span>
  );
}

export function Segmented<T extends string>(props: { options: { value: T; label: string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {props.options.map((o) => (
        <button key={o.value} className={o.value === props.value ? "on" : ""} onClick={() => props.onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Pager(props: { page: number; pages: number; onPage: (p: number) => void; total?: number }) {
  if (props.pages <= 1) return null;
  return (
    <div className="pager">
      {props.total !== undefined && <span className="mono">{props.total.toLocaleString()} rows</span>}
      <button className="btn sm" disabled={props.page <= 1} onClick={() => props.onPage(1)}>
        «
      </button>
      <button className="btn sm" disabled={props.page <= 1} onClick={() => props.onPage(props.page - 1)}>
        ‹
      </button>
      <span className="mono">
        {props.page} / {props.pages}
      </span>
      <button className="btn sm" disabled={props.page >= props.pages} onClick={() => props.onPage(props.page + 1)}>
        ›
      </button>
      <button className="btn sm" disabled={props.page >= props.pages} onClick={() => props.onPage(props.pages)}>
        »
      </button>
    </div>
  );
}

export function Drawer(props: { open: boolean; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    if (props.open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.open, props.onClose]);
  if (!props.open) return null;
  return (
    <>
      <div className="drawer-backdrop" onClick={props.onClose} />
      <aside className="drawer">{props.children}</aside>
    </>
  );
}

export function SevTiles(props: { critical: number; high: number; medium: number; low: number; compact?: boolean }) {
  const cells: Array<[string, number]> = [
    ["critical", props.critical],
    ["high", props.high],
    ["medium", props.medium],
    ["low", props.low],
  ];
  return (
    <div className="grid cols-4" style={{ gap: 8 }}>
      {cells.map(([k, v]) => (
        <div key={k} className={`sev ${k}`}>
          <div className="n">{v}</div>
          <div className="l">{k}</div>
        </div>
      ))}
    </div>
  );
}

/** Horizontal usage bar row (validated chart palette; color follows entity, not rank). */
export function HBar(props: { label: string; value: number; max: number; color?: string; onClick?: () => void }) {
  const pct = props.max > 0 ? Math.max(2, (props.value / props.max) * 100) : 0;
  return (
    <div className="hbar" onClick={props.onClick} style={props.onClick ? { cursor: "pointer" } : undefined}>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={props.label}>
        {props.label}
      </span>
      <div className="track">
        <div className="fill" style={{ width: `${pct}%`, background: props.color ?? "var(--chart-1)" }} />
      </div>
      <span className="val">{props.value.toLocaleString()}</span>
    </div>
  );
}

export function Empty(props: { children: ReactNode }) {
  return <div className="empty">{props.children}</div>;
}

export function Spinner(props: { label?: string }) {
  return (
    <div className="empty">
      <span className="caret" /> {props.label ?? "Loading…"}
    </div>
  );
}
