import { useEffect, useState } from 'react';
import { DESKTOP_COMPANION_TEXT_EVENT } from '../../../events/bus';

export type DesktopCompanionTextNotice = {
  sessionId: string;
  observationId: string;
  turnId: string;
  content: string;
  priority: 'normal' | 'critical';
  expiresAtMs: number;
};

/** The companion's latest text remark, shown until it expires or is dismissed. */
export function DesktopCompanionTextSurface() {
  const [notice, setNotice] = useState<DesktopCompanionTextNotice | null>(null);

  useEffect(() => {
    const handleText = (event: Event) => {
      const next = normalizeDesktopCompanionTextNotice((event as CustomEvent<unknown>).detail);
      if (next) setNotice(next);
    };
    window.addEventListener(DESKTOP_COMPANION_TEXT_EVENT, handleText);
    return () => window.removeEventListener(DESKTOP_COMPANION_TEXT_EVENT, handleText);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), Math.max(0, notice.expiresAtMs - Date.now()));
    return () => window.clearTimeout(timer);
  }, [notice]);

  const visible = notice !== null && Date.now() < notice.expiresAtMs;
  return (
    <section className="desktop-companion-text-surface" data-omnix-desktop-companion-text="true" data-priority={notice?.priority ?? 'normal'} hidden={!visible}>
      <div className="desktop-companion-text-surface__header">
        <strong>Companion</strong>
        <button type="button" onClick={() => setNotice(null)}>Dismiss</button>
      </div>
      <p className="desktop-companion-text-surface__content">{visible ? notice.content : ''}</p>
    </section>
  );
}

export function normalizeDesktopCompanionTextNotice(value: unknown): DesktopCompanionTextNotice | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const input = value as Record<string, unknown>;
  const sessionId = typeof input.sessionId === 'string' ? input.sessionId.trim() : '';
  const observationId = typeof input.observationId === 'string' ? input.observationId.trim() : '';
  const turnId = typeof input.turnId === 'string' ? input.turnId.trim() : '';
  const content = typeof input.content === 'string' ? input.content.replace(/\s+/g, ' ').trim().slice(0, 500) : '';
  const expiresAtMs = Number(input.expiresAtMs);
  if (!sessionId || !observationId || !turnId || !content || !Number.isFinite(expiresAtMs)) return null;
  return {
    sessionId,
    observationId,
    turnId,
    content,
    expiresAtMs,
    priority: input.priority === 'critical' ? 'critical' : 'normal',
  };
}
