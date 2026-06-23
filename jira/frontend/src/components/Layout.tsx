import { Outlet } from 'react-router-dom';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import { CreateIssueModal } from './CreateIssueModal';
import { useUI } from '../store/ui';

export function Layout() {
  const { createIssueOpen, createIssueProjectId, closeCreateIssue, bumpIssueCreated } = useUI();

  return (
    <div className="app-shell">
      <TopBar />
      <div className="app-body">
        <Sidebar />
        <main className="main-content">
          <Outlet />
        </main>
      </div>
      <CreateIssueModal
        open={createIssueOpen}
        onClose={closeCreateIssue}
        defaultProjectId={createIssueProjectId}
        onCreated={() => bumpIssueCreated()}
      />
    </div>
  );
}
