export function relTime(iso: string | undefined | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  let s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  s /= 60;
  if (s < 60) return `${Math.round(s)}m ago`;
  s /= 60;
  if (s < 48) return `${Math.round(s)}h ago`;
  s /= 24;
  if (s < 21) return `${Math.round(s)}d ago`;
  if (s < 60) return `${Math.round(s / 7)}w ago`;
  if (s < 550) return `${Math.round(s / 30.4)}mo ago`;
  return `${Math.round(s / 365)}y ago`;
}

export function fmtDt(iso: string | undefined | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Africa/Cairo",
  });
}

export function fmtNum(n: number | undefined | null): string {
  if (n === undefined || n === null) return "—";
  return n.toLocaleString("en-US");
}

export const STATUS_TONE: Record<string, "ok" | "warn" | "err" | "skip"> = {
  ok: "ok",
  success: "ok",
  approved: "ok",
  done: "ok",
  failed: "err",
  error: "err",
  rejected: "err",
  running: "warn",
  pending: "warn",
  paused: "warn",
  open: "warn",
  skip: "skip",
  idle: "skip",
};
