import type { LiveVoiceTranscriptState } from '../workspace';
import type { ChatMessage } from './chatbotWorkspaceModel';
import type { LiveChatMessage } from './LiveChatFullscreenShell';

/** Reading chat messages for display: attachments and times. */
export const MAX_CHAT_IMAGE_ATTACHMENTS = 8;
export const SUPPORTED_CHAT_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

export function formatMessageTime(value: string): string { if (value.includes('T')) return value.slice(11, 16); return value; }
export function chatImageDataUrls(metadata?: Record<string, unknown>): string[] {
  const candidates: unknown[] = [];
  if (Array.isArray(metadata?.image_data_urls)) candidates.push(...metadata.image_data_urls);
  if (metadata?.image_data_url) candidates.unshift(metadata.image_data_url);
  const images: string[] = [];
  for (const value of candidates) {
    if (typeof value !== 'string') continue;
    if (![...SUPPORTED_CHAT_IMAGE_TYPES].some((mimeType) => value.startsWith(`data:${mimeType};base64,`))) continue;
    if (!images.includes(value)) images.push(value);
    if (images.length >= MAX_CHAT_IMAGE_ATTACHMENTS) break;
  }
  return images;
}
export function chatTextAttachment(metadata?: Record<string, unknown>): { filename: string; mimeType: string } | null { const value = metadata?.text_attachment; if (!value || typeof value !== 'object' || Array.isArray(value)) return null; const attachment = value as Record<string, unknown>; const filename = typeof attachment.filename === 'string' ? attachment.filename.trim() : ''; const mimeType = typeof attachment.mime_type === 'string' ? attachment.mime_type.trim() : ''; const text = typeof attachment.text === 'string' ? attachment.text : ''; return filename && mimeType && text ? { filename, mimeType } : null; }

/** The newer of the send result and the queried session, never another session than the selected one. */
export function selectFreshChatSession<T extends { id?: string; message_count?: number; messages?: unknown[] } | null | undefined>(
  mutationSession: T,
  queriedSession: T,
): T {
  if (!mutationSession) return queriedSession;
  if (!queriedSession) return mutationSession;
  // A mutation result remains available after the user switches sessions.
  // It must never replace the newly selected session, even when it is newer.
  if (mutationSession.id !== queriedSession.id) return queriedSession;
  const mutationCount = mutationSession.message_count ?? mutationSession.messages?.length ?? 0;
  const queryCount = queriedSession.message_count ?? queriedSession.messages?.length ?? 0;
  if (queryCount !== mutationCount) return queryCount > mutationCount ? queriedSession : mutationSession;

  // Some responses carry the total message_count but only a partial messages
  // projection. When the reported counts tie, prefer the snapshot that can
  // actually render more of the transcript.
  const mutationMessageLength = mutationSession.messages?.length ?? 0;
  const queryMessageLength = queriedSession.messages?.length ?? 0;
  return queryMessageLength >= mutationMessageLength ? queriedSession : mutationSession;
}

/** Immersive Live Chat shows the chat when it has messages, the live transcript otherwise. */
export function liveChatMessagesFor(messages: ChatMessage[], transcript: LiveVoiceTranscriptState): LiveChatMessage[] {
  if (messages.length) {
    return messages.map((message) => ({
      id: message.id,
      role: message.role === 'user' ? 'user' : message.role === 'assistant' ? 'assistant' : 'system',
      text: message.content,
      timestamp: message.created_at,
    }));
  }
  const rows: LiveChatMessage[] = transcript.rows.map((row) => ({
    id: row.id,
    role: row.speaker === 'You' ? 'user' : 'assistant',
    text: row.text,
    timestamp: row.at,
  }));
  if (transcript.delivery) {
    rows.push({ id: 'live-voice-delivery', role: 'assistant', text: transcript.delivery.text, timestamp: null });
  }
  return rows;
}
