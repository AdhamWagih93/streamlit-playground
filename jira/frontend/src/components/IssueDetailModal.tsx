import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { IssueDetailView } from './IssueDetailView';

interface Props {
  issueKey: string;
  onClose: () => void;
  onChanged?: () => void;
}

export function IssueDetailModal({ issueKey, onClose, onChanged }: Props) {
  // Mount guard so the modal animates in cleanly.
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(true), []);

  return (
    <Modal open={open} onClose={onClose} title={issueKey} size="wide">
      <IssueDetailView issueKey={issueKey} onChanged={onChanged} />
    </Modal>
  );
}
