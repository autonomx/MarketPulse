import { Link } from '@tanstack/react-router';
import { WorkspacePanel } from '../design/primitives';

/** An address that matches no workspace (WP-9.7: no silent fallback to Chat). */
export function NotFoundView() {
  return (
    <WorkspacePanel label="Page not found">
      <h2>Page not found</h2>
      <p className="workspace-summary">No workspace lives at this address.</p>
      <Link to="/chatbot">Open Chat</Link>
    </WorkspacePanel>
  );
}
