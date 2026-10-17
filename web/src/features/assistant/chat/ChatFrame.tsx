import type { ReactNode } from 'react';
import { assistantSidebarItems, type AssistantView, type UtilityPanel } from './chatbotWorkspaceModel';

type ChatNavigationSidebarProps = {
  minimized: boolean;
  onToggleMinimized: () => void;
  activeView: AssistantView;
  onShowView: (view: AssistantView) => void;
  /** The session list, below the views. */
  children: ReactNode;
};

/** Chat's left sidebar: the workspace views, then the sessions. */
export function ChatNavigationSidebar({ minimized, onToggleMinimized, activeView, onShowView, children }: ChatNavigationSidebarProps) {
  return (
    <aside className={`assistant-chat-sidebar${minimized ? ' assistant-chat-sidebar-minimized' : ''}`} aria-label="Omnix assistant navigation">
      <div role="group" className="assistant-chat-sidebar-controls" aria-label="Assistant navigation controls">
        <button
          type="button"
          className="assistant-chat-sidebar-minimize"
          aria-label={minimized ? 'Expand assistant sidebar' : 'Minimize assistant sidebar'}
          aria-pressed={minimized}
          title={minimized ? 'Expand assistant sidebar' : 'Minimize assistant sidebar'}
          onClick={onToggleMinimized}
        >
          <span aria-hidden="true" data-direction={minimized ? 'right' : 'left'}>{minimized ? '›' : '‹'}</span>
        </button>
      </div>
      <nav className="assistant-sidebar-nav" aria-label="Assistant workspace">
        {assistantSidebarItems.map((item) => (
          <button aria-label={`Open ${item.label} view`} className={activeView === item.id ? 'active' : undefined} key={item.id} onClick={() => onShowView(item.id)} title={item.label} type="button">
            <span aria-hidden="true">{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      {children}
    </aside>
  );
}

type ChatUtilitySidebarProps = {
  minimized: boolean;
  onToggleMinimized: () => void;
  activePanel: UtilityPanel;
  onShowVoice: () => void;
  /** The live voice card and the tool execution card. */
  children: ReactNode;
};

/** Chat's right sidebar: live voice and tool execution. */
export function ChatUtilitySidebar({ minimized, onToggleMinimized, activePanel, onShowVoice, children }: ChatUtilitySidebarProps) {
  return (
    <aside className={`assistant-chat-side${minimized ? ' assistant-chat-side-minimized' : ''}`} aria-label="Live voice and tool execution">
      <div role="group" className="assistant-side-panel-toggle" aria-label="Assistant utility panel">
        <button type="button" className={activePanel === 'voice' ? 'assistant-side-panel-option active' : 'assistant-side-panel-option'} onClick={onShowVoice}>Live Voice</button>
        <button
          type="button"
          className="assistant-side-panel-minimize"
          aria-label={minimized ? 'Expand side panel' : 'Minimize side panel'}
          aria-pressed={minimized}
          title={minimized ? 'Expand side panel' : 'Minimize side panel'}
          onClick={onToggleMinimized}
        >
          <span aria-hidden="true">{minimized ? '‹' : '›'}</span>
        </button>
      </div>
      <div className="assistant-live-tools-grid" data-active-panel={activePanel}>
        {children}
      </div>
    </aside>
  );
}

type ChatHeaderProps = {
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  /** The identity control: who answers, in which voice. */
  children: ReactNode;
};

/** The header above the transcript: the identity control and full screen. */
export function ChatHeader({ fullscreen, onToggleFullscreen, children }: ChatHeaderProps) {
  const label = fullscreen ? 'Exit full screen chat' : 'Enter full screen chat';
  return (
    <header className="assistant-chat-header">
      <div className="assistant-chat-header-actions assistant-chat-integrated-actions">
        {children}
        <button type="button" className="assistant-header-pill assistant-chat-fullscreen-button" aria-label={label} aria-pressed={fullscreen} title={label} onClick={onToggleFullscreen}>
          {fullscreen ? '↙' : '⛶'}
        </button>
      </div>
    </header>
  );
}
