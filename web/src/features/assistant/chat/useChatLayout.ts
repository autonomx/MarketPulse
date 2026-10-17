import { useEffect, useState } from 'react';
import {
  ASSISTANT_SIDEBAR_STORAGE_KEY,
  ASSISTANT_SIDE_PANEL_STORAGE_KEY,
  ASSISTANT_VIEW_STORAGE_KEY,
  assistantSidebarItems,
  type AssistantView,
  type UtilityPanel,
} from './chatbotWorkspaceModel';

/** Chat's layout: the active view, full screen, and the two collapsible side panels (persisted per browser). */
export function useChatLayout(initialToolId: string | null) {
  const [activeView, setActiveView] = useState<AssistantView>(() => {
    if (initialToolId) return 'tools';
    const stored = window.localStorage.getItem(ASSISTANT_VIEW_STORAGE_KEY);
    return assistantSidebarItems.some((item) => item.id === stored) ? stored as AssistantView : 'chats';
  });
  const [isChatFullscreen, setIsChatFullscreen] = useState(false);
  const [activeUtilityPanel, setActiveUtilityPanel] = useState<UtilityPanel>('voice');
  const [isAssistantSidebarMinimized, setIsAssistantSidebarMinimized] = useState(() => {
    try {
      return window.localStorage.getItem(ASSISTANT_SIDEBAR_STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });
  const [isSidePanelMinimized, setIsSidePanelMinimized] = useState(() => {
    try {
      return window.localStorage.getItem(ASSISTANT_SIDE_PANEL_STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    window.localStorage.setItem(ASSISTANT_VIEW_STORAGE_KEY, activeView);
  }, [activeView]);

  useEffect(() => {
    try {
      window.localStorage.setItem(ASSISTANT_SIDE_PANEL_STORAGE_KEY, String(isSidePanelMinimized));
    } catch {
      // Ignore local storage failures; the panel remains usable for this session.
    }
  }, [isSidePanelMinimized]);

  useEffect(() => {
    try {
      window.localStorage.setItem(ASSISTANT_SIDEBAR_STORAGE_KEY, String(isAssistantSidebarMinimized));
    } catch {
      // Ignore local storage failures; the navigation remains usable for this session.
    }
  }, [isAssistantSidebarMinimized]);

  useEffect(() => {
    if (activeView !== 'chats') setIsChatFullscreen(false);
  }, [activeView]);

  useEffect(() => {
    if (!isChatFullscreen) return;
    const previousOverflow = document.body.style.overflow;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setIsChatFullscreen(false);
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isChatFullscreen]);

  return {
    activeView, setActiveView,
    isChatFullscreen, setIsChatFullscreen,
    activeUtilityPanel, setActiveUtilityPanel,
    isAssistantSidebarMinimized, setIsAssistantSidebarMinimized,
    isSidePanelMinimized, setIsSidePanelMinimized,
  };
}
