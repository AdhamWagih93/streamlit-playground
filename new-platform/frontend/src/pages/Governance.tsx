import { useState } from "react";

import { Segmented } from "../components/ui";
import { AdoPanel } from "./gov/AdoPanel";
import { GlossaryPanel } from "./gov/GlossaryPanel";
import { HistoryPanel } from "./gov/HistoryPanel";
import { LdapPanel, SyncInventoryPanel, SyncPostgresPanel } from "./gov/SyncPanels";
import { ToolAccessPanel } from "./gov/ToolAccessPanel";

type TabKey = "git_es" | "inv_pg" | "ldap" | "ado" | "history" | "tools" | "glossary";

const TABS: { value: TabKey; label: string }[] = [
  { value: "git_es", label: "Sync git↔ES" },
  { value: "inv_pg", label: "Inventory↔PG" },
  { value: "ldap", label: "LDAP" },
  { value: "ado", label: "ADO Coverage" },
  { value: "history", label: "History→PG" },
  { value: "tools", label: "Tool Access" },
  { value: "glossary", label: "Glossary" },
];

export default function Governance() {
  const [tab, setTab] = useState<TabKey>("git_es");

  return (
    <>
      <div className="reveal" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div className="card-kicker">GOVERNANCE — STORES RECONCILED, ACCESS AUDITED</div>
        <Segmented options={TABS} value={tab} onChange={setTab} />
      </div>

      <div className="reveal" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {tab === "git_es" && <SyncInventoryPanel />}
        {tab === "inv_pg" && <SyncPostgresPanel />}
        {tab === "ldap" && <LdapPanel />}
        {tab === "ado" && <AdoPanel />}
        {tab === "history" && <HistoryPanel />}
        {tab === "tools" && <ToolAccessPanel />}
        {tab === "glossary" && <GlossaryPanel />}
      </div>
    </>
  );
}
