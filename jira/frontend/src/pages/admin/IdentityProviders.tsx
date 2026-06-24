import { useEffect, useState } from 'react';
import { IdentityProvider, IdentityProviderPayload, ProviderType } from '../../types';
import {
  listIdentityProviders,
  createIdentityProvider,
  updateIdentityProvider,
  deleteIdentityProvider,
  testIdentityProvider,
} from '../../api/admin';
import { Modal } from '../../components/Modal';
import { SpinnerCenter } from '../../components/Spinner';
import { EmptyState } from '../../components/EmptyState';
import { Toast, ToastMsg } from '../../components/Toast';
import { apiErrorMessage } from '../../api/client';

export function IdentityProviders() {
  const [items, setItems] = useState<IdentityProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [toast, setToast] = useState<ToastMsg | null>(null);
  const [editing, setEditing] = useState<IdentityProvider | null>(null);
  const [creating, setCreating] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);

  function load() {
    setLoading(true);
    listIdentityProviders()
      .then(setItems)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function runTest(p: IdentityProvider) {
    setTestingId(p.id);
    try {
      const res = await testIdentityProvider(p.id);
      setToast({ ok: res.ok, text: res.message });
    } catch (e) {
      setToast({ ok: false, text: apiErrorMessage(e, 'Test failed') });
    } finally {
      setTestingId(null);
    }
  }

  async function toggleEnabled(p: IdentityProvider) {
    try {
      await updateIdentityProvider(p.id, { enabled: !p.enabled });
      load();
    } catch (e) {
      setToast({ ok: false, text: apiErrorMessage(e) });
    }
  }

  async function remove(p: IdentityProvider) {
    if (!confirm(`Delete identity provider “${p.name}”?`)) return;
    await deleteIdentityProvider(p.id).catch((e) => setToast({ ok: false, text: apiErrorMessage(e) }));
    load();
  }

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 760 }}>
      <div className="row-between mb-16">
        <div>
          <h2 className="page-title" style={{ fontSize: 19 }}>Identity Providers</h2>
          <p className="muted text-sm">Configure LDAP / Microsoft Entra sign-in and provisioning.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>+ Add provider</button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      {items.length === 0 ? (
        <EmptyState icon="🔑" title="No identity providers" message="Add a provider to enable external sign-in." />
      ) : (
        items.map((p) => (
          <div className="list-row" key={p.id}>
            <div className="list-row-main">
              <div className="row gap-8">
                <span className="list-row-title">{p.name}</span>
                <span className="badge">{p.provider_type === 'ldap' ? 'LDAP' : 'Entra'}</span>
                {p.enabled ? <span className="badge badge-ok">enabled</span> : <span className="badge">disabled</span>}
              </div>
              <div className="muted text-xs">
                {p.provider_type === 'ldap'
                  ? `${p.ldap_host || ''}${p.ldap_port ? ':' + p.ldap_port : ''}`
                  : `tenant ${p.entra_tenant_id || '—'}`}
                {p.auto_provision_users && ' · auto-provision'}
                {p.sync_groups && ' · sync groups'}
              </div>
            </div>
            <div className="row gap-8">
              <button className="btn btn-sm" onClick={() => runTest(p)} disabled={testingId === p.id}>
                {testingId === p.id ? 'Testing…' : 'Test'}
              </button>
              <button className="btn btn-sm" onClick={() => toggleEnabled(p)}>{p.enabled ? 'Disable' : 'Enable'}</button>
              <button className="btn btn-sm" onClick={() => setEditing(p)}>Edit</button>
              <button className="btn btn-ghost btn-sm" onClick={() => remove(p)}>×</button>
            </div>
          </div>
        ))
      )}

      {(creating || editing) && (
        <ProviderModal
          provider={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); load(); }}
          onError={(t) => setToast({ ok: false, text: t })}
        />
      )}

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}

function ProviderModal({
  provider,
  onClose,
  onSaved,
  onError,
}: {
  provider: IdentityProvider | null;
  onClose: () => void;
  onSaved: () => void;
  onError: (t: string) => void;
}) {
  const [name, setName] = useState(provider?.name || '');
  const [type, setType] = useState<ProviderType>(provider?.provider_type || 'ldap');
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [autoProvision, setAutoProvision] = useState(provider?.auto_provision_users ?? false);
  const [syncGroups, setSyncGroups] = useState(provider?.sync_groups ?? false);
  const [order, setOrder] = useState(provider?.order ?? 0);
  const [busy, setBusy] = useState(false);

  // LDAP
  const [ldapHost, setLdapHost] = useState(provider?.ldap_host || '');
  const [ldapPort, setLdapPort] = useState(provider?.ldap_port ?? 389);
  const [ldapUseSsl, setLdapUseSsl] = useState(provider?.ldap_use_ssl ?? false);
  const [ldapBindDn, setLdapBindDn] = useState(provider?.ldap_bind_dn || '');
  const [ldapBindPassword, setLdapBindPassword] = useState('');
  const [ldapUserBaseDn, setLdapUserBaseDn] = useState(provider?.ldap_user_base_dn || '');
  const [ldapUserFilter, setLdapUserFilter] = useState(provider?.ldap_user_filter || '');
  const [attrUsername, setAttrUsername] = useState(provider?.ldap_attr_username || '');
  const [attrEmail, setAttrEmail] = useState(provider?.ldap_attr_email || '');
  const [attrDisplay, setAttrDisplay] = useState(provider?.ldap_attr_display_name || '');
  const [groupBaseDn, setGroupBaseDn] = useState(provider?.ldap_group_base_dn || '');
  const [groupFilter, setGroupFilter] = useState(provider?.ldap_group_filter || '');
  const [attrGroupName, setAttrGroupName] = useState(provider?.ldap_attr_group_name || '');

  // Entra
  const [tenantId, setTenantId] = useState(provider?.entra_tenant_id || '');
  const [clientId, setClientId] = useState(provider?.entra_client_id || '');
  const [clientSecret, setClientSecret] = useState('');
  const [redirectUri, setRedirectUri] = useState(provider?.entra_redirect_uri || '');
  const [scopes, setScopes] = useState(provider?.entra_scopes || '');

  async function save() {
    setBusy(true);
    const payload: IdentityProviderPayload = {
      name,
      provider_type: type,
      enabled,
      auto_provision_users: autoProvision,
      sync_groups: syncGroups,
      order,
    };
    if (type === 'ldap') {
      Object.assign(payload, {
        ldap_host: ldapHost,
        ldap_port: ldapPort,
        ldap_use_ssl: ldapUseSsl,
        ldap_bind_dn: ldapBindDn,
        ldap_user_base_dn: ldapUserBaseDn,
        ldap_user_filter: ldapUserFilter,
        ldap_attr_username: attrUsername,
        ldap_attr_email: attrEmail,
        ldap_attr_display_name: attrDisplay,
        ldap_group_base_dn: groupBaseDn,
        ldap_group_filter: groupFilter,
        ldap_attr_group_name: attrGroupName,
      });
      if (ldapBindPassword) payload.ldap_bind_password = ldapBindPassword;
    } else {
      Object.assign(payload, {
        entra_tenant_id: tenantId,
        entra_client_id: clientId,
        entra_redirect_uri: redirectUri,
        entra_scopes: scopes,
      });
      if (clientSecret) payload.entra_client_secret = clientSecret;
    }
    try {
      if (provider) await updateIdentityProvider(provider.id, payload);
      else await createIdentityProvider(payload);
      onSaved();
    } catch (e) {
      onError(apiErrorMessage(e, 'Could not save provider'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={provider ? 'Edit provider' : 'Add identity provider'}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !name}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      <div className="field-grid">
        <div className="field">
          <label>Name</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label>Type</label>
          <select className="select" value={type} onChange={(e) => setType(e.target.value as ProviderType)} disabled={!!provider}>
            <option value="ldap">LDAP</option>
            <option value="entra">Microsoft Entra</option>
          </select>
        </div>
      </div>

      <div className="row gap-16 wrap mb-16">
        <label className="row gap-8"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled</label>
        <label className="row gap-8"><input type="checkbox" checked={autoProvision} onChange={(e) => setAutoProvision(e.target.checked)} /> Auto-provision users</label>
        <label className="row gap-8"><input type="checkbox" checked={syncGroups} onChange={(e) => setSyncGroups(e.target.checked)} /> Sync groups</label>
        <label className="row gap-8" style={{ width: 100 }}>Order <input className="input" type="number" value={order} onChange={(e) => setOrder(Number(e.target.value))} /></label>
      </div>

      {type === 'ldap' ? (
        <>
          <div className="field-grid">
            <div className="field"><label>Host</label><input className="input" value={ldapHost} onChange={(e) => setLdapHost(e.target.value)} /></div>
            <div className="field"><label>Port</label><input className="input" type="number" value={ldapPort} onChange={(e) => setLdapPort(Number(e.target.value))} /></div>
          </div>
          <label className="row gap-8 mb-16"><input type="checkbox" checked={ldapUseSsl} onChange={(e) => setLdapUseSsl(e.target.checked)} /> Use SSL (LDAPS)</label>
          <div className="field"><label>Bind DN</label><input className="input" value={ldapBindDn} onChange={(e) => setLdapBindDn(e.target.value)} /></div>
          <div className="field">
            <label>Bind password {provider?.ldap_bind_password_set && <span className="badge badge-info">set</span>}</label>
            <input className="input" type="password" value={ldapBindPassword} onChange={(e) => setLdapBindPassword(e.target.value)} placeholder={provider?.ldap_bind_password_set ? 'Leave blank to keep' : ''} />
          </div>
          <div className="field-grid">
            <div className="field"><label>User base DN</label><input className="input" value={ldapUserBaseDn} onChange={(e) => setLdapUserBaseDn(e.target.value)} /></div>
            <div className="field"><label>User filter</label><input className="input" value={ldapUserFilter} onChange={(e) => setLdapUserFilter(e.target.value)} placeholder="(uid=%s)" /></div>
            <div className="field"><label>Username attr</label><input className="input" value={attrUsername} onChange={(e) => setAttrUsername(e.target.value)} placeholder="uid" /></div>
            <div className="field"><label>Email attr</label><input className="input" value={attrEmail} onChange={(e) => setAttrEmail(e.target.value)} placeholder="mail" /></div>
            <div className="field"><label>Display name attr</label><input className="input" value={attrDisplay} onChange={(e) => setAttrDisplay(e.target.value)} placeholder="cn" /></div>
            <div className="field"><label>Group base DN</label><input className="input" value={groupBaseDn} onChange={(e) => setGroupBaseDn(e.target.value)} /></div>
            <div className="field"><label>Group filter</label><input className="input" value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)} /></div>
            <div className="field"><label>Group name attr</label><input className="input" value={attrGroupName} onChange={(e) => setAttrGroupName(e.target.value)} placeholder="cn" /></div>
          </div>
        </>
      ) : (
        <>
          <div className="field"><label>Tenant ID</label><input className="input" value={tenantId} onChange={(e) => setTenantId(e.target.value)} /></div>
          <div className="field"><label>Client ID</label><input className="input" value={clientId} onChange={(e) => setClientId(e.target.value)} /></div>
          <div className="field">
            <label>Client secret {provider?.entra_client_secret_set && <span className="badge badge-info">set</span>}</label>
            <input className="input" type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} placeholder={provider?.entra_client_secret_set ? 'Leave blank to keep' : ''} />
          </div>
          <div className="field"><label>Redirect URI</label><input className="input" value={redirectUri} onChange={(e) => setRedirectUri(e.target.value)} placeholder={`${window.location.origin}/auth/callback`} /></div>
          <div className="field"><label>Scopes</label><input className="input" value={scopes} onChange={(e) => setScopes(e.target.value)} placeholder="openid profile email" /></div>
        </>
      )}
    </Modal>
  );
}
