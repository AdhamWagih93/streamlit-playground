import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "../lib/auth";
import { IntegrationStrip } from "./IntegrationStrip";
import "./shell.css";

const NAV: Array<{ section: string; items: Array<{ to: string; label: string; glyph: string; admin?: boolean }> }> = [
  {
    section: "Platform",
    items: [
      { to: "/", label: "Overview", glyph: "◈" },
      { to: "/fleet", label: "Delivery Fleet", glyph: "❖" },
      { to: "/events", label: "Event Log", glyph: "⧗" },
      { to: "/actions", label: "Actions", glyph: "▶" },
      { to: "/security", label: "Security", glyph: "⛨" },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { to: "/incidents", label: "Incident Analysis", glyph: "✦" },
      { to: "/assistant", label: "Assistant", glyph: "✧" },
      { to: "/architecture", label: "Architecture", glyph: "⌬", admin: true },
    ],
  },
  {
    section: "Governance",
    items: [
      { to: "/technology", label: "Tech & Platforms", glyph: "🧬", admin: true },
      { to: "/teams", label: "Teams & Members", glyph: "◎", admin: true },
      { to: "/people", label: "People Insights", glyph: "☺", admin: true },
      { to: "/governance", label: "Governance", glyph: "⚖", admin: true },
      { to: "/settings", label: "Settings", glyph: "⚙", admin: true },
    ],
  },
];

const ROLE_PRESETS: Record<string, { roles: string[]; teams: string[] }> = {
  Admin: { roles: ["admin"], teams: ["Platform"] },
  CLevel: { roles: ["clevel"], teams: [] },
  Developer: { roles: ["developer"], teams: ["Payments"] },
  QC: { roles: ["quality-control"], teams: ["QA-Central"] },
  Operations: { roles: ["operations"], teams: ["CoreBanking"] },
};

function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="mono topbar-clock">
      {now.toLocaleTimeString("en-GB", { timeZone: "Africa/Cairo", hour12: false })} <span className="dim">CAI</span>
    </span>
  );
}

function Breadcrumb() {
  const { pathname } = useLocation();
  const label =
    NAV.flatMap((s) => s.items).find((i) => i.to === pathname)?.label ??
    (pathname === "/" ? "Overview" : pathname.replace("/", ""));
  return (
    <span className="crumb">
      <span className="dim">meridian /</span> {label}
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { me, devSwitch } = useAuth();
  const [switcher, setSwitcher] = useState(false);
  if (!me) return null;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <svg viewBox="0 0 32 32" width="26" height="26">
              <rect width="32" height="32" rx="7" fill="var(--gold)" />
              <circle cx="16" cy="16" r="9" fill="none" stroke="#0B1020" strokeWidth="2" />
              <path d="M16 7v18M8.5 12h15M8.5 20h15" stroke="#0B1020" strokeWidth="2" fill="none" />
            </svg>
          </span>
          <span>
            <div className="brand-name">MERIDIAN</div>
            <div className="brand-sub">Engineering Platform</div>
          </span>
        </div>
        <nav>
          {NAV.map((s) => {
            const items = s.items.filter((i) => !i.admin || me.is_admin);
            if (!items.length) return null;
            return (
              <div key={s.section} className="nav-section">
                <div className="nav-section-label">{s.section}</div>
                {items.map((i) => (
                  <NavLink key={i.to} to={i.to} end={i.to === "/"} className={({ isActive }) => `nav-item ${isActive ? "on" : ""}`}>
                    <span className="nav-glyph">{i.glyph}</span>
                    {i.label}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <footer className="sidebar-foot">
          <span className="tag teal">GOVERNED · ON-PREM AI</span>
          {me.data_mode === "demo" && <span className="tag gold">DEMO DATA</span>}
        </footer>
      </aside>

      <div className="main">
        <header className="topbar">
          <Breadcrumb />
          <div className="topbar-right">
            <span className="tag blue">tenant enterprise-core</span>
            <Clock />
            <div className="userchip" onClick={() => me.auth_mode === "none" && setSwitcher((v) => !v)}
                 title={me.auth_mode === "none" ? "Dev mode: click to switch role" : me.email}>
              <span className="avatar">{me.display_name.split(" ").map((w) => w[0]).slice(0, 2).join("")}</span>
              <span>
                <div className="user-name">{me.display_name}</div>
                <div className="user-role mono">
                  {me.role}
                  {me.teams.length > 0 && ` · ${me.teams.join(", ")}`}
                </div>
              </span>
              {me.auth_mode === "none" && <span className="dim">▾</span>}
            </div>
            {switcher && (
              <div className="switcher card">
                <div className="card-kicker">DEV MODE — preview as</div>
                {Object.entries(ROLE_PRESETS).map(([name, p]) => (
                  <button key={name} className={`btn sm ${me.role === name ? "primary" : "ghost"}`}
                          onClick={() => devSwitch(p.roles, p.teams)}>
                    {name}
                    {p.teams.length > 0 && <span className="dim"> · {p.teams.join(",")}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </header>
        <main className="content">
          <IntegrationStrip />
          {children}
        </main>
      </div>
    </div>
  );
}
