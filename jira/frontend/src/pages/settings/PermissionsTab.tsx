import { useEffect, useMemo, useState } from 'react';
import {
  ProjectOut,
  PermissionScheme,
  PermissionSchemeDetail,
  PermissionCatalog,
  Role,
} from '../../types';
import {
  listPermissionSchemes,
  setProjectScheme,
  getPermissionScheme,
  getPermissionCatalog,
  listRoles,
} from '../../api/rbac';
import { SpinnerCenter } from '../../components/Spinner';
import { apiErrorMessage } from '../../api/client';

interface Props {
  project: ProjectOut;
  canAdmin: boolean;
  setError: (s: string) => void;
  onSchemeChanged: () => void;
}

export function PermissionsTab({ project, canAdmin, setError, onSchemeChanged }: Props) {
  const currentSchemeId = project.permission_scheme_id != null ? String(project.permission_scheme_id) : '';
  const currentSchemeName = project.permission_scheme?.name || 'Default Permission Scheme';

  const [schemes, setSchemes] = useState<PermissionScheme[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [catalog, setCatalog] = useState<PermissionCatalog | null>(null);
  const [detail, setDetail] = useState<PermissionSchemeDetail | null>(null);
  const [schemeId, setSchemeId] = useState<string>(currentSchemeId);
  const [loading, setLoading] = useState(true);
  const [savingScheme, setSavingScheme] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Load scheme list + catalog + roles once.
  useEffect(() => {
    Promise.all([listPermissionSchemes(), getPermissionCatalog(), listRoles()])
      .then(([s, c, r]) => {
        setSchemes(s);
        setCatalog(c);
        setRoles(r);
      })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  // Load the capability detail for whichever scheme is currently chosen.
  useEffect(() => {
    if (!schemeId) {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    getPermissionScheme(schemeId)
      .then(setDetail)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoadingDetail(false));
  }, [schemeId]);

  const catalogMap = useMemo(() => {
    const map = new Map<string, string>();
    catalog?.project_permissions.forEach((p) => map.set(p.key, p.description));
    return map;
  }, [catalog]);

  // Group role-held grants by role name -> friendly permission labels.
  const byRole = useMemo(() => {
    const groups = new Map<string, string[]>();
    if (!detail) return groups;
    for (const g of detail.grants) {
      if (g.holder_type !== 'role') continue;
      const label = catalogMap.get(g.permission) || g.permission;
      const arr = groups.get(g.holder_value) || [];
      if (!arr.includes(label)) arr.push(label);
      groups.set(g.holder_value, arr);
    }
    return groups;
  }, [detail, catalogMap]);

  async function saveScheme() {
    setSavingScheme(true);
    try {
      await setProjectScheme(project.id, schemeId || null);
      onSchemeChanged();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not assign scheme'));
    } finally {
      setSavingScheme(false);
    }
  }

  if (loading) return <SpinnerCenter />;

  // Roles to show in the capability summary — prefer defined roles, fall back
  // to whatever holder names appear in the grants.
  const roleNames = roles.length > 0 ? roles.map((r) => r.name) : Array.from(byRole.keys());

  return (
    <div style={{ maxWidth: 760 }}>
      <div className="section-head">
        <h2 className="section-head-title">Permissions</h2>
        <p className="section-head-sub">
          Permissions define what each role can do. They come from this project's permission scheme,
          which instance admins manage globally — here you choose which scheme applies.
        </p>
      </div>

      <div className="section-card">
        <h3>Permission scheme</h3>
        <p className="muted text-sm mb-8">
          Current scheme: <strong>{currentSchemeName}</strong>
        </p>
        {canAdmin ? (
          <div className="row gap-8 wrap" style={{ alignItems: 'flex-end' }}>
            <select className="select" value={schemeId} onChange={(e) => setSchemeId(e.target.value)} style={{ minWidth: 260 }}>
              <option value="">Default Permission Scheme</option>
              {schemes.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}{s.is_default ? ' (default)' : ''}
                </option>
              ))}
            </select>
            <button className="btn btn-primary" onClick={saveScheme} disabled={savingScheme || schemeId === currentSchemeId}>
              {savingScheme ? 'Saving…' : 'Apply scheme'}
            </button>
          </div>
        ) : (
          <div className="callout callout-muted">Only project admins can change the permission scheme.</div>
        )}
      </div>

      <div className="section-card">
        <h3>What each role can do</h3>
        <p className="muted text-sm mb-8">
          A read-only summary of the capabilities granted to each role by{' '}
          <strong>{schemeId ? (schemes.find((s) => String(s.id) === schemeId)?.name ?? currentSchemeName) : 'the default scheme'}</strong>.
          Assign people to these roles in the <strong>People</strong> tab.
        </p>

        {loadingDetail ? (
          <SpinnerCenter />
        ) : !schemeId || byRole.size === 0 ? (
          <div className="callout callout-muted">
            This scheme grants permissions by group/user rather than by role, or no role grants are defined.
          </div>
        ) : (
          <div className="capability-grid">
            {roleNames
              .filter((name) => byRole.has(name))
              .map((name) => (
                <div className="capability-card" key={name}>
                  <div className="row gap-8 mb-8">
                    <span className="role-badge">{name}</span>
                    <span className="muted text-xs">{byRole.get(name)?.length ?? 0} permissions</span>
                  </div>
                  <ul className="capability-list">
                    {(byRole.get(name) || []).map((label) => (
                      <li key={label}>{label}</li>
                    ))}
                  </ul>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
