import { Navigate, NavLink, Routes, Route } from 'react-router-dom';
import { useAuth } from '../store/auth';
import { Insights } from './admin/Insights';
import { MailSettings } from './admin/MailSettings';
import { AuthSettings } from './admin/AuthSettings';
import { JiraConnections } from './admin/JiraConnections';
import { IdentityProviders } from './admin/IdentityProviders';
import { Groups } from './admin/Groups';
import { GlobalPermissions } from './admin/GlobalPermissions';
import { PermissionSchemes } from './admin/PermissionSchemes';

const SECTIONS: { to: string; label: string }[] = [
  { to: 'insights', label: 'Insights' },
  { to: 'auth', label: 'Authentication' },
  { to: 'mail', label: 'Mail' },
  { to: 'identity-providers', label: 'Identity Providers' },
  { to: 'jira-connections', label: 'Jira Connections' },
  { to: 'groups', label: 'Groups' },
  { to: 'global-permissions', label: 'Global Permissions' },
  { to: 'permission-schemes', label: 'Permission Schemes' },
];

export function AdminPage() {
  const user = useAuth((s) => s.user);
  if (!user?.is_admin) return <Navigate to="/projects" replace />;

  return (
    <div className="page">
      <div className="breadcrumb">Administration</div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Site administration</h1>
          <div className="page-subtitle">Instance-wide settings — visible to site admins only.</div>
        </div>
      </div>

      <div className="admin-layout">
        <nav className="admin-subnav">
          <div className="admin-subnav-group">
            <div className="admin-subnav-heading">GLOBAL CONFIGURATION</div>
            <div className="admin-subnav-subtitle">Instance-wide settings — apply to every project.</div>
          </div>
          {SECTIONS.map((s) => (
            <NavLink key={s.to} to={s.to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
              {s.label}
            </NavLink>
          ))}
        </nav>

        <div className="admin-main">
          <Routes>
            <Route index element={<Navigate to="insights" replace />} />
            <Route path="insights" element={<Insights />} />
            <Route path="mail" element={<MailSettings />} />
            <Route path="auth" element={<AuthSettings />} />
            <Route path="jira-connections" element={<JiraConnections />} />
            <Route path="identity-providers" element={<IdentityProviders />} />
            <Route path="groups" element={<Groups />} />
            <Route path="global-permissions" element={<GlobalPermissions />} />
            <Route path="permission-schemes" element={<PermissionSchemes />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}
