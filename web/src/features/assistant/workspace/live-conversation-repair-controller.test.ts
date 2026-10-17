import { describe, expect, it } from 'vitest';

import { injectRepairIntoRequest } from './live-conversation-repair-controller';
import type { LiveConversationRepairContext } from './live-conversation-repair';

const repair: LiveConversationRepairContext = {
  kind: 'acknowledge_correction',
  instruction: 'Acknowledge the correction briefly and continue.',
  source_reason: 'correction',
  confidence: 0.9,
};

describe('injectRepairIntoRequest', () => {
  it('adds repair context to the next assistant-context POST without changing visible content', () => {
    const input = '/api/assistant/context/chat/sessions/chat%3Aone/messages/stream';
    const result = injectRepairIntoRequest(input, {
      method: 'POST',
      body: JSON.stringify({ content: 'Actually, I meant Tuesday.', provider_id: 'local' }),
    }, repair);

    expect(result.consumed).toBe(true);
    const payload = JSON.parse(String(result.init?.body));
    expect(payload.content).toBe('Actually, I meant Tuesday.');
    expect(payload.live_repair).toEqual(repair);
  });

  it('does not consume repair for unrelated or malformed requests', () => {
    expect(injectRepairIntoRequest('/api/providers', { method: 'GET' }, repair).consumed).toBe(false);
    expect(injectRepairIntoRequest('/api/assistant/context/chat/sessions/chat%3Aone/messages', {
      method: 'POST', body: 'not-json',
    }, repair).consumed).toBe(false);
    expect(injectRepairIntoRequest('/api/chat/sessions/chat%3Aone/messages/stream', {
      method: 'POST', body: JSON.stringify({ content: 'Hello' }),
    }, repair).consumed).toBe(false);
  });
});
