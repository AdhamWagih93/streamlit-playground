import { create } from 'zustand';

interface UIState {
  createIssueOpen: boolean;
  createIssueProjectId?: string;
  openCreateIssue: (projectId?: string) => void;
  closeCreateIssue: () => void;
  // bumped after a successful create so listeners can refetch
  issueCreatedTick: number;
  bumpIssueCreated: () => void;
}

export const useUI = create<UIState>((set) => ({
  createIssueOpen: false,
  createIssueProjectId: undefined,
  openCreateIssue: (projectId) => set({ createIssueOpen: true, createIssueProjectId: projectId }),
  closeCreateIssue: () => set({ createIssueOpen: false }),
  issueCreatedTick: 0,
  bumpIssueCreated: () => set((s) => ({ issueCreatedTick: s.issueCreatedTick + 1 })),
}));
