import { ApiError } from '../../../api/errors';
import {
  adoptActiveSession,
  assistantContextStore,
  deferResearchModePersistence,
  dispatchPerformance,
  stopDesktopShare,
  storeLocalWorkspace,
  type AssistantContextState,
} from './assistant-context-store';
import type { DesktopTemporalCapture } from './desktop-temporal-capture';

/** The route a chat message goes to: the chat route, or the context route with the tools' fields. */
export type AssistantChatRoute = 'chat' | 'context';

type DesktopFrames = Awaited<ReturnType<DesktopTemporalCapture['buildPayload']>>;

/**
 * Sends one chat message the way the active context tools need it (WP-9.5
 * replaced the fetch middleware that rewrote chat requests with this call).
 * With research, agent mode, desktop sharing or a local folder on, the
 * message goes to the context route with those tools' fields; otherwise,
 * or when the gateway has no context route, to the chat route. The
 * session's research mode is saved after the request settles, so saving it
 * never delays the response.
 */
export async function sendChatWithAssistantContext<T>(
  sessionId: string,
  body: Record<string, unknown>,
  send: (route: AssistantChatRoute, body: Record<string, unknown>) => Promise<T>,
): Promise<T> {
  adoptActiveSession(sessionId);
  const context = assistantContextStore.getState();
  const { agentMode, researchMode, desktopShare, localWorkspace } = context;
  if (localWorkspace) storeLocalWorkspace(sessionId, localWorkspace);
  const enhanced = agentMode || researchMode !== 'disabled' || desktopShare !== null || localWorkspace !== null;
  const request = enhanced ? sendThroughContext(sessionId, body, context, send) : send('chat', body);
  if (!enhanced) {
    dispatchPerformance('assistant_context_chat_request_dispatched', { sessionId, agentMode, researchMode, enhanced, persistenceDeferred: true });
  }
  deferResearchModePersistence(request, sessionId, researchMode);
  return request;
}

async function sendThroughContext<T>(
  sessionId: string,
  body: Record<string, unknown>,
  context: AssistantContextState,
  send: (route: AssistantChatRoute, body: Record<string, unknown>) => Promise<T>,
): Promise<T> {
  const { agentMode, researchMode, deepResearchMaxPages, localWorkspace } = context;
  const desktop = await captureDesktop(context);
  dispatchPerformance('assistant_context_chat_request_dispatched', { sessionId, agentMode, researchMode, enhanced: true, persistenceDeferred: true });
  try {
    return await send('context', {
      ...body,
      agent_mode: agentMode ? true : body.agent_mode,
      dry_run: agentMode ? false : body.dry_run,
      web_research_mode: researchMode,
      deep_research_max_pages: researchMode === 'deep' ? deepResearchMaxPages : undefined,
      workspace_root: localWorkspace?.path,
      desktop_current_image_data_url: desktop?.currentImageDataUrl,
      desktop_history_image_data_url: desktop?.historyImageDataUrl,
      desktop_combined_image_data_url: desktop?.combinedImageDataUrl,
      desktop_history_timestamps: desktop?.historyTimestamps ?? [],
      desktop_capture_mode: desktop?.captureMode ?? 'single',
    });
  } catch (error) {
    // A gateway without the context route still takes the message as a plain chat message.
    if (error instanceof ApiError && error.status === 404) return send('chat', body);
    throw error;
  }
}

/** The shared desktop's frames for this message; a failed capture stops sharing and the message goes without them. */
async function captureDesktop({ desktopShare }: AssistantContextState): Promise<DesktopFrames | undefined> {
  if (!desktopShare) return undefined;
  try {
    const payload = await desktopShare.capture.buildPayload();
    assistantContextStore.update({
      desktopStatus: payload.captureMode === 'temporal'
        ? `${payload.selectedHistoryFrames} history + current`
        : 'Current frame attached',
    });
    return payload;
  } catch (error) {
    stopDesktopShare({ resetStatus: false });
    assistantContextStore.update({ desktopStatus: error instanceof Error ? error.message : 'Capture failed' });
    return undefined;
  }
}
