import { useEffect, useRef, type UIEvent } from 'react';
import { isScrolledNearBottom } from './chatbotWorkspaceModel';

/**
 * Keeps the transcript scrolled to the newest message while the reader is
 * at the bottom; a reader who scrolled up stays where they are. A newly
 * opened session starts at its newest message.
 */
export function useStickToLatestMessage(displayedSessionId: string, messageCount: number) {
  const messagesContainerRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const lastMessageScrollKeyRef = useRef('');
  const shouldStickToLatestMessageRef = useRef(true);

  useEffect(() => {
    if (messageCount === 0) return;
    const scrollKey = `${displayedSessionId}:${messageCount}`;
    const alreadyInSession = lastMessageScrollKeyRef.current.startsWith(`${displayedSessionId}:`);
    lastMessageScrollKeyRef.current = scrollKey;
    if (alreadyInSession && !shouldStickToLatestMessageRef.current) return;
    const scheduleFrame: (callback: FrameRequestCallback) => number = typeof window.requestAnimationFrame === 'function'
      ? window.requestAnimationFrame.bind(window)
      : (callback: FrameRequestCallback) => Number(window.setTimeout(() => callback(performance.now()), 0));
    const cancelFrame = typeof window.cancelAnimationFrame === 'function'
      ? window.cancelAnimationFrame.bind(window)
      : (id: number) => window.clearTimeout(id);
    const frameId = scheduleFrame(() => {
      messagesEndRef.current?.scrollIntoView({ block: 'end', behavior: 'auto' });
      shouldStickToLatestMessageRef.current = true;
    });
    return () => cancelFrame(frameId);
  }, [displayedSessionId, messageCount]);

  function handleMessagesScroll(event: UIEvent<HTMLDivElement>): void {
    shouldStickToLatestMessageRef.current = isScrolledNearBottom(event.currentTarget);
  }

  return { messagesContainerRef, messagesEndRef, handleMessagesScroll };
}
