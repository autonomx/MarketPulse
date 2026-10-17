import { memo, useLayoutEffect, useMemo, useRef } from 'react';
import type { components } from '../api/generated';
import { ChatResponseMetricsRow } from './chat-response-metrics-controller';
import { chatImageDataUrls, chatTextAttachment, formatMessageTime } from './chatMessageModel';
import { LiveAgentToolProposalCard, liveAgentToolProposals } from './LiveAgentToolProposalCard';
import { isDeepResearchMessage, renderMarkdownHtml, renderResearchReportHtml } from './markdownRenderer';
import { OmnixRunCard } from './OmnixRunCard';
import { handleResearchReportAction } from './researchProgressController';
import { ResearchMessageDetails } from './ResearchProgressCard';

type ChatMessage = components['schemas']['ChatMessage'];
export type ChatMessageFeedback = 'liked' | 'disliked';

/** What a message's buttons do; Chat passes one stable object for every message. */
export type ChatMessageActions = {
  toggleFeedback: (messageId: string, feedback: ChatMessageFeedback) => void;
  copy: (message: ChatMessage) => void;
  play: (text: string) => void;
  stream: (message: ChatMessage) => void;
  toggleMenu: (messageId: string) => void;
  closeMenu: () => void;
  continueFrom: (message: ChatMessage) => void;
  openTools: () => void;
};

type ChatMessageItemProps = {
  message: ChatMessage;
  sessionId: string;
  feedback: ChatMessageFeedback | undefined;
  menuOpen: boolean;
  streaming: boolean;
  actions: ChatMessageActions;
};

/** One message of the transcript; it re-renders only when its own props change (WP-9.5). */
export const ChatMessageItem = memo(function ChatMessageItem({ message, sessionId, feedback, menuOpen, streaming, actions }: ChatMessageItemProps) {
  const research = isDeepResearchMessage(message.metadata);
  const html = useMemo(
    () => research ? renderResearchReportHtml(message.content, message.metadata) : renderMarkdownHtml(message.content, message.metadata),
    [message.content, message.metadata, research],
  );
  const images = chatImageDataUrls(message.metadata);
  const attachment = chatTextAttachment(message.metadata);
  const assistant = message.role === 'assistant';
  const streamLabel = streaming ? 'Stop streaming response audio' : 'Stream response audio';
  return (
    <article className={`assistant-chat-message ${message.role}`}>
      {message.role !== 'user' ? <span className="assistant-chat-avatar" aria-hidden="true" /> : null}
      <div className="assistant-chat-bubble">
        <header><strong>{assistant ? 'personality' : message.role === 'user' ? 'You' : message.role}</strong><time dateTime={message.created_at}>{formatMessageTime(message.created_at)}</time></header>
        {images.length ? (
          <div className="assistant-chat-message-images">
            {images.map((dataUrl, index) => <img className="assistant-chat-message-image" src={dataUrl} alt={index === 0 ? 'User-provided attachment' : `User-provided attachment ${index + 1}`} key={`${message.id}:image:${index}`} />)}
          </div>
        ) : null}
        {attachment ? <div className="assistant-chat-file-attachment"><strong>Attached file: {attachment.filename}</strong><small>{attachment.mimeType}</small></div> : null}
        <div
          className={`assistant-message-content${research ? ' assistant-research-report-host' : ''}`}
          data-omnix-message-content="true"
          onClick={(event) => handleResearchReportAction(event.nativeEvent)}
          data-raw-content={message.content}
          data-message-id={message.id}
          dangerouslySetInnerHTML={{ __html: html }}
        />
        {liveAgentToolProposals(message.metadata).map((proposal) => <LiveAgentToolProposalCard key={proposal.proposal_id} proposal={proposal} sessionId={sessionId} onOpenTools={actions.openTools} />)}
        <OmnixRunCard metadata={message.metadata} />
        {assistant ? <ChatResponseMetricsRow metadata={message.metadata} /> : null}
        {assistant ? (
          <div role="group" className="assistant-message-actions" aria-label="Assistant message actions">
            <button type="button" className={feedback === 'liked' ? 'active' : undefined} aria-label="Like response" aria-pressed={feedback === 'liked'} onClick={() => actions.toggleFeedback(message.id, 'liked')}>♡</button>
            <button type="button" className={feedback === 'disliked' ? 'active' : undefined} aria-label="Dislike response" aria-pressed={feedback === 'disliked'} onClick={() => actions.toggleFeedback(message.id, 'disliked')}>↯</button>
            <button type="button" aria-label="Copy response" onClick={() => actions.copy(message)}>□</button>
            <button type="button" aria-label="Play response audio" onClick={() => actions.play(message.content)}>▶</button>
            <button type="button" data-omnix-stream-audio="true" aria-label={streamLabel} title={streamLabel} aria-pressed={streaming} onClick={() => actions.stream(message)}>{streaming ? '■' : '≋'}</button>
            <button type="button" aria-label="More response actions" aria-expanded={menuOpen} onClick={() => actions.toggleMenu(message.id)}>⋮</button>
            {menuOpen ? (
              <div className="assistant-message-action-menu" role="menu">
                <button type="button" role="menuitem" onClick={() => actions.copy(message)}>Copy text</button>
                <button type="button" role="menuitem" onClick={() => { actions.closeMenu(); actions.play(message.content); }}>Play audio</button>
                <button type="button" role="menuitem" onClick={() => { actions.closeMenu(); actions.continueFrom(message); }}>Continue</button>
              </div>
            ) : null}
          </div>
        ) : null}
        {assistant ? <ResearchMessageDetails message={message} /> : null}
      </div>
    </article>
  );
});

/** One stable actions object that calls the latest handlers, so memoized messages do not re-render. */
export function useStableMessageActions(actions: ChatMessageActions): ChatMessageActions {
  const latest = useRef(actions);
  useLayoutEffect(() => {
    latest.current = actions;
  });
  return useMemo<ChatMessageActions>(() => ({
    toggleFeedback: (messageId, feedback) => latest.current.toggleFeedback(messageId, feedback),
    copy: (message) => latest.current.copy(message),
    play: (text) => latest.current.play(text),
    stream: (message) => latest.current.stream(message),
    toggleMenu: (messageId) => latest.current.toggleMenu(messageId),
    closeMenu: () => latest.current.closeMenu(),
    continueFrom: (message) => latest.current.continueFrom(message),
    openTools: () => latest.current.openTools(),
  }), []);
}
