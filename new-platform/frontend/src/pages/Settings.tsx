import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Card, Chip, Drawer, Empty, Spinner, Tag } from "../components/ui";
import { apiGet, apiPost, apiPut, apiDelete } from "../lib/api";
import { relTime } from "../lib/format";

type Field = { name: string; label: string; type: string; required?: boolean; default?: unknown };
type Integration = {
  key: string;
  role: string;
  tool: string;
  glyph: string;
  fields: Field[];
  config: Record<string, unknown>;
  configured: boolean;
  enabled: boolean;
  updated_at: string | null;
  updated_by: string | null;
  last_test_status: "ok" | "failed" | "never";
  last_test_detail: string;
  last_test_at: string | null;
  used_by: string[];
  optional_for: string[];
};
type Payload = {
  storage: { persistent: boolean; detail: string; derived_key: boolean };
  data_mode: string;
  postgres: { role: string; tool: string; glyph: string; configured: boolean; source: string; detail: string };
  integrations: Integration[];
};

function statusTone(i: Integration): "ok" | "warn" | "err" | "skip" {
  if (!i.configured) return "skip";
  if (!i.enabled) return "warn";
  if (i.last_test_status === "failed") return "err";
  return "ok";
}

function statusLabel(i: Integration): string {
  if (!i.configured) return "not configured";
  if (!i.enabled) return "disabled";
  if (i.last_test_status === "ok") return `test ok · ${relTime(i.last_test_at)}`;
  if (i.last_test_status === "failed") return `test failed · ${relTime(i.last_test_at)}`;
  return "configured · untested";
}

function ConfigForm(props: { integration: Integration; onClose: () => void }) {
  const { integration: it } = props;
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const v: Record<string, unknown> = {};
    for (const f of it.fields) {
      const cur = it.config[f.name];
      if (f.type === "password") v[f.name] = "";
      else v[f.name] = cur ?? f.default ?? (f.type === "bool" ? false : "");
    }
    return v;
  });
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const save = useMutation({
    mutationFn: () => apiPut(`/settings/integrations/${it.key}`, { config: values }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings-integrations"] });
      qc.invalidateQueries({ queryKey: ["requirements"] });
    },
  });
  const test = useMutation({
    mutationFn: () => apiPost<{ ok: boolean; detail: string }>(`/settings/integrations/${it.key}/test`),
    onSuccess: (r) => {
      setTestResult(r);
      qc.invalidateQueries({ queryKey: ["settings-integrations"] });
    },
    onError: (e) => setTestResult({ ok: false, detail: String((e as Error).message) }),
  });
  const remove = useMutation({
    mutationFn: () => apiDelete(`/settings/integrations/${it.key}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings-integrations"] });
      qc.invalidateQueries({ queryKey: ["requirements"] });
      props.onClose();
    },
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <div className="card-kicker">
          {it.glyph} {it.tool}
        </div>
        <h3>{it.role}</h3>
      </div>
      {it.fields.map((f) => (
        <label key={f.name} style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5 }}>
          <span style={{ color: "var(--ink-2)" }}>
            {f.label}
            {f.required && <span style={{ color: "var(--gold)" }}> *</span>}
            {f.type === "password" && (it.config[f.name] as { set?: boolean } | undefined)?.set && (
              <Tag tone="teal"> currently set — leave blank to keep</Tag>
            )}
          </span>
          {f.type === "bool" ? (
            <button
              className={`btn sm ${values[f.name] ? "primary" : ""}`}
              style={{ alignSelf: "flex-start" }}
              onClick={() => setValues((v) => ({ ...v, [f.name]: !v[f.name] }))}
            >
              {values[f.name] ? "enabled" : "disabled"}
            </button>
          ) : (
            <input
              className="input"
              type={f.type === "password" ? "password" : "text"}
              placeholder={f.type === "password" ? "••••••••" : String(f.default ?? "")}
              value={String(values[f.name] ?? "")}
              onChange={(e) =>
                setValues((v) => ({ ...v, [f.name]: f.type === "number" ? Number(e.target.value) || e.target.value : e.target.value }))
              }
              autoComplete="off"
            />
          )}
        </label>
      ))}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save (encrypted)"}
        </button>
        <button className="btn" disabled={test.isPending || !it.configured} onClick={() => test.mutate()}
                title={it.configured ? "Probe the connection" : "Save a config first"}>
          {test.isPending ? "Testing…" : "⚡ Test connection"}
        </button>
        <button className="btn danger sm" style={{ marginLeft: "auto" }}
                disabled={remove.isPending || !it.configured} onClick={() => remove.mutate()}>
          Remove
        </button>
      </div>
      {save.isSuccess && <Chip tone="ok">saved — stored encrypted in the platform database</Chip>}
      {save.isError && <Chip tone="err">save failed: {String((save.error as Error).message)}</Chip>}
      {testResult && (
        <Chip tone={testResult.ok ? "ok" : "err"}>
          {testResult.ok ? "connection ok" : "connection failed"} — {testResult.detail}
        </Chip>
      )}

      {(it.used_by.length > 0 || it.optional_for.length > 0) && (
        <Card kicker="POWERS" title="Features depending on this">
          <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5 }}>
            {it.used_by.map((u) => (
              <div key={u}>
                <Tag tone="gold">required</Tag> {u}
              </div>
            ))}
            {it.optional_for.map((u) => (
              <div key={u}>
                <Tag>enhances</Tag> {u}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings-integrations"], queryFn: () => apiGet<Payload>("/settings/integrations") });
  const [openKey, setOpenKey] = useState<string | null>(null);
  const toggle = useMutation({
    mutationFn: (p: { key: string; enabled: boolean }) => apiPost(`/settings/integrations/${p.key}/enabled`, { enabled: p.enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings-integrations"] });
      qc.invalidateQueries({ queryKey: ["requirements"] });
    },
  });

  if (q.isLoading) return <Spinner label="Loading integration settings…" />;
  if (!q.data) return <Empty>Settings unavailable.</Empty>;
  const d = q.data;
  const open = d.integrations.find((i) => i.key === openKey) ?? null;

  return (
    <>
      {!d.storage.persistent && (
        <Card className="reveal" gold>
          <Chip tone="err">settings storage is NOT persistent</Chip>{" "}
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
            The platform database is unreachable ({d.storage.detail}). Integration configs are held in process
            memory and will be lost on restart — set <code>DATABASE_URL</code> in .env.
          </span>
        </Card>
      )}
      {d.storage.derived_key && (
        <Card className="reveal">
          <Chip tone="warn">dev encryption key</Chip>{" "}
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
            <code>SETTINGS_ENCRYPTION_KEY</code> is not set — configs are encrypted with a key derived from the
            session secret. Fine locally; set a dedicated key in deployment.
          </span>
        </Card>
      )}

      <Card className="reveal" kicker="PLATFORM DATABASE — CONFIGURED VIA ENVIRONMENT" title={<>▣ {d.postgres.role}</>}
            actions={<Tag tone={d.postgres.configured ? "teal" : "err"}>{d.postgres.configured ? "configured" : "not configured"}</Tag>}>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          {d.postgres.tool} · {d.postgres.source} · <span className="mono">{d.postgres.detail}</span>
          <br />
          This is the only integration defined outside the platform. Every other integration below is stored{" "}
          <strong>encrypted</strong> inside it.
        </div>
      </Card>

      <div className="grid cols-2 reveal">
        {d.integrations.map((it) => (
          <Card key={it.key} kicker={it.tool.toUpperCase()} title={<>{it.glyph} {it.role}</>}
                actions={
                  <>
                    <Chip tone={statusTone(it)}>{statusLabel(it)}</Chip>
                    {it.configured && (
                      <button className="btn sm ghost" onClick={() => toggle.mutate({ key: it.key, enabled: !it.enabled })}>
                        {it.enabled ? "disable" : "enable"}
                      </button>
                    )}
                    <button className="btn sm" onClick={() => setOpenKey(it.key)}>
                      {it.configured ? "⚙ Edit" : "＋ Configure"}
                    </button>
                  </>
                }>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {it.used_by.map((u) => (
              <Tag key={u} tone={it.configured && it.enabled ? "teal" : "err"} title={`Required by: ${u}`}>
                {u}
              </Tag>
            ))}
            {it.optional_for.map((u) => (
              <Tag key={u} title={`Enhances: ${u}`}>{u}</Tag>
            ))}
          </div>
          {it.updated_at && (
            <div className="mono" style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 8 }}>
              updated {relTime(it.updated_at)} by {it.updated_by}
              {it.last_test_detail && ` · last test: ${it.last_test_detail}`}
            </div>
          )}
          </Card>
        ))}
      </div>

      <Drawer open={open !== null} onClose={() => setOpenKey(null)}>
        {open && <ConfigForm key={open.key} integration={open} onClose={() => setOpenKey(null)} />}
      </Drawer>
    </>
  );
}
