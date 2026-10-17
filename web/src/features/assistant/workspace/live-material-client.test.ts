/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { describe, expect, it, vi } from 'vitest';

import { LiveMaterialClient } from './live-material-client';

function response(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  }));
}

describe('LiveMaterialClient', () => {
  it('appends ephemeral non-generating material by default', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => response({
      segment_id: 's0',
      accepted_sequence: 0,
      context_version: 1,
      task_contract_id: 'default',
      task_contract_version: 1,
      retention: 'ephemeral_session',
      response_policy: 'none',
      idempotent: false,
      exact_segment_count: 1,
      exact_text_chars: 11,
      security: {
        instruction_authority: 'none',
        tool_eligibility: 'none',
        memory_write_eligibility: false,
        task_contract_mutation: false,
      },
    }));
    const client = new LiveMaterialClient(fetchMock as typeof fetch);

    const ack = await client.append('chat:one', { segment_id: 's0', sequence: 0, text: 'hello world' });

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe('/api/chat/sessions/chat%3Aone/live/material');
    expect(JSON.parse(String(init?.body))).toMatchObject({
      segment_id: 's0',
      sequence: 0,
      response_policy: 'none',
      retention: 'ephemeral_session',
      task_contract_id: 'default',
      task_contract_version: 1,
    });
    expect(ack.security).toEqual({
      instruction_authority: 'none',
      tool_eligibility: 'none',
      memory_write_eligibility: false,
      task_contract_mutation: false,
    });
  });

  it('requires an explicit promotion request', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => response({
      session_id: 'chat:one', context_version: 2, retention: 'durable_conversation', content: 'draft', content_chars: 5,
    }));
    const client = new LiveMaterialClient(fetchMock as typeof fetch);

    await client.promote('chat:one', 'durable_conversation');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/sessions/chat%3Aone/live/material/promote',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ retention: 'durable_conversation' }) }),
    );
  });
});
