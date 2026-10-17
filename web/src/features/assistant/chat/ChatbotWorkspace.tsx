// Chat's stylesheets, in cascade order; they load before the stylesheets of the
// components imported below.
import './chat-sidebar-manager.css';
import './researchProgressController.css';
import './ChatWorkspaceLayout.css';
import './ChatbotWorkspaceTools.css';
import './ChatbotWorkspaceUtilityToggle.css';
import '../workspace/assistant-context-controller.css';
import '../workspace/desktop-companion-controls.css';
import '../workspace/desktop-companion-text-surface.css';
import '../workspace/research-release-controller.css';
import './ChatbotWorkspaceFullscreen.css';
import type { OmnixModuleDefinition } from '../../../app/modules';
import { WorkspacePanel } from '../../../design/primitives';
import { ToolExecutionPanel } from '../workspace';
import { LiveChatFullscreenShell } from './LiveChatFullscreenShell';
import { LiveChatPanel } from './LiveChatPanel';
import { ChatSidebarSessions } from './ChatSidebarSessions';
import { ChatNavigationSidebar, ChatUtilitySidebar } from './ChatFrame';
import { useChatWorkspace } from './useChatWorkspace';
import { ChatConversation, ChatOtherViews, ChatInlineStatus, ChatLiveVoicePanel } from './ChatWorkspacePanels';

export function ChatbotWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const ws = useChatWorkspace(module);
  const {
    activeUtilityPanel, activeView, chatSessions, deleteSessionMutation, isAssistantSidebarMinimized,
    isChatFullscreen, isSidePanelMinimized, liveChatMessages, selectSidebarSession, selectedSessionId,
    sendFromLiveChat, sessionsError, sessionsLoading, setActiveUtilityPanel, setIsAssistantSidebarMinimized,
    setIsSidePanelMinimized, setSelectedSessionId, showAssistantView, toggleLiveCallFromControls, toolExecutionRows,
  } = ws;
  return (
    <WorkspacePanel labelledBy="module-title" className={`assistant-chat-page${isChatFullscreen ? ' assistant-chat-page-fullscreen' : ''}`}>
      <h2 id="module-title" className="workspace-module-heading">{module.label}</h2>
      <div className={`assistant-chat-layout${isAssistantSidebarMinimized ? ' assistant-chat-layout-sidebar-minimized' : ''}${isSidePanelMinimized ? ' assistant-chat-layout-side-minimized' : ''}`}>
        <ChatNavigationSidebar
          minimized={isAssistantSidebarMinimized}
          onToggleMinimized={() => setIsAssistantSidebarMinimized((current) => !current)}
          activeView={activeView}
          onShowView={showAssistantView}
        >
          <ChatSidebarSessions
            sessions={chatSessions}
            loading={sessionsLoading}
            failed={sessionsError}
            selectedSessionId={selectedSessionId}
            onSelect={selectSidebarSession}
            onDelete={(session) => deleteSessionMutation.mutateAsync(session.id)}
          />
        </ChatNavigationSidebar>

        <section className="assistant-chat-main" aria-labelledby="module-title">
          {activeView === 'chats' ? (
            <>
              <ChatConversation ws={ws} />
            </>
          ) : activeView === 'live' ? (
            <LiveChatPanel sessionId={selectedSessionId} onSessionResolved={setSelectedSessionId} onToggleCall={toggleLiveCallFromControls} />
          ) : (
            <ChatOtherViews ws={ws} />
          )}
          <ChatInlineStatus ws={ws} />
        </section>
        <ChatUtilitySidebar
          minimized={isSidePanelMinimized}
          onToggleMinimized={() => setIsSidePanelMinimized((current) => !current)}
          activePanel={activeUtilityPanel}
          onShowVoice={() => setActiveUtilityPanel('voice')}
        >
          <ChatLiveVoicePanel ws={ws} />
            <section className="assistant-tool-sidebar-card" aria-labelledby="assistant-tool-execution-heading"><ToolExecutionPanel rows={toolExecutionRows} title="Tool execution" description="Review approvals and monitor tool execution results." /></section>
        </ChatUtilitySidebar>
      </div>
      <LiveChatFullscreenShell messages={liveChatMessages} onSendMessage={sendFromLiveChat} onToggleCall={toggleLiveCallFromControls} />
    </WorkspacePanel>
  );
}
