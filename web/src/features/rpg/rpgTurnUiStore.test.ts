import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getRpgTurnDiagnostics,
  markRpgTurnReactCommitted,
  markRpgTurnVisible,
} from './rpgTurnDiagnostics';
import {
  beginRpgTurnUiSubmission,
  completeRpgTurnUiSubmission,
  createRpgSubmissionId,
  getRpgTurnUiEntries,
  installRpgTurnUiFetchInterceptor,
  mergeRpgTurnUiMessages,
  refreshPathsForChangedDomains,
  resetRpgTurnUiStoreForTests,
  storyMessageIdentity,
} from './rpgTurnUiStore';
import { pipelineFetch } from '../../api/fetchPipeline';

afterEach(() => {
  resetRpgTurnUiStoreForTests();
  vi.restoreAllMocks();
});

describe('rpgTurnUiStore', () => {
  it('creates stable server idempotency keys', () => {
    expect(createRpgSubmissionId()).toMatch(/^submit:[a-z0-9]+$/i);
  });

  it('does not truncate durable history to the latest ten messages', () => {
    const baseMessages = Array.from({ length: 24 }, (_, index) => ({
      id: `interaction:${index + 1}:player`,
      interactionId: `interaction:${index + 1}`,
      avatar: 'A',
      speaker: 'Alyndra (You)',
      text: `Turn ${index + 1}`,
      tone: 'player' as const,
    }));

    const merged = mergeRpgTurnUiMessages(baseMessages, [{
      id: 'interaction:25:player',
      sessionId: 'session:history',
      submissionId: 'submit:25',
      interactionId: 'interaction:25',
      status: 'complete',
      avatar: 'A',
      speaker: 'Alyndra (You)',
      text: 'Turn 25',
      tone: 'player',
      messageKind: 'player',
      messageIndex: 0,
    }]);

    expect(merged).toHaveLength(24);
    expect(merged[0].text).toBe('Turn 1');
    expect(merged[23].text).toBe('Turn 25');
  });

  it('shows the player command immediately and rekeys the completed turn by interaction id', () => {
    beginRpgTurnUiSubmission({
      sessionId: 'session:bran',
      submissionId: 'submit:one',
      command: 'I ask Bran how business is doing.',
    });

    expect(getRpgTurnUiEntries('session:bran').map((entry) => entry.text)).toEqual([
      'I ask Bran how business is doing.',
      'Considering the scene…',
    ]);

    completeRpgTurnUiSubmission({
      sessionId: 'session:bran',
      submissionId: 'submit:one',
      payload: {
        ok: true,
        contract_version: 'rpg_turn_response_v2',
        interaction_id: 'interaction:1',
        visible_response: {
          narration: 'Bran rests the polishing rag on the counter.',
          messages: [
            {
              kind: 'npc_dialogue',
              speaker_id: 'npc:bran',
              speaker: 'Bran',
              text: 'Steady enough, though the old road has been quiet.',
            },
          ],
        },
      },
    });

    const entries = getRpgTurnUiEntries('session:bran');
    expect(entries.map((entry) => entry.id)).toEqual([
      'interaction:1:player',
      'interaction:1:narration',
      'interaction:1:message:0',
    ]);
    expect(entries.map((entry) => entry.text)).toEqual([
      'I ask Bran how business is doing.',
      'Bran rests the polishing rag on the counter.',
      'Steady enough, though the old road has been quiet.',
    ]);
    expect(entries.every((entry) => entry.status === 'complete')).toBe(true);
    expect(entries.every((entry) => entry.interactionId === 'interaction:1')).toBe(true);
  });

  it('adds submission and client timing headers, then records browser/server diagnostics', async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get('X-Omnix-Rpg-Submission-Id')).toMatch(/^submit:/);
      expect(headers.get('X-Omnix-Rpg-Client-Started')).toMatch(/T/);
      return new Response(JSON.stringify({
        ok: true,
        contract_version: 'rpg_turn_response_v2',
        session_id: 'session:bran',
        interaction_id: 'interaction:2',
        trace_id: 'trace:2',
        visible_response: {
          narration: 'Bran glances toward the window.',
          messages: [{ kind: 'npc_dialogue', speaker: 'Bran', text: 'The road is quieter than it should be.' }],
        },
        state: { changed: true, changed_domains: ['conversation'] },
        timing: { pipeline_before_encode_ms: 12.5 },
        performance: { attribution_percent: 98.5 },
      }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'X-Omnix-Rpg-Trace-Id': 'trace:2',
          'X-Omnix-Rpg-Response-Bytes': '777',
          'X-Omnix-Rpg-Attribution-Pct': '98.5',
          'Server-Timing': 'rpg_0_turn_apply;dur=10.000',
        },
      });
    });
    installRpgTurnUiFetchInterceptor(fetchImpl as typeof fetch);

    await pipelineFetch('/api/rpg/sessions/session%3Abran/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'How is the road?' }),
    });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(getRpgTurnUiEntries('session:bran').map((entry) => entry.text)).toEqual([
      'How is the road?',
      'Bran glances toward the window.',
      'The road is quieter than it should be.',
    ]);
    const diagnostic = getRpgTurnDiagnostics('session:bran')[0];
    expect(diagnostic.traceId).toBe('trace:2');
    expect(diagnostic.interactionId).toBe('interaction:2');
    expect(diagnostic.responseBytes).toBe(777);
    expect(diagnostic.serverAttributionPercent).toBe(98.5);
    expect(diagnostic.serverPayloadTiming).toEqual({ pipeline_before_encode_ms: 12.5 });
    expect(diagnostic.client.requestToHeadersMs).toBeGreaterThanOrEqual(0);
    expect(diagnostic.client.headersToBodyMs).toBeGreaterThanOrEqual(0);
    expect(diagnostic.client.bodyToParseMs).toBeGreaterThanOrEqual(0);

    markRpgTurnReactCommitted('session:bran', ['interaction:2']);
    markRpgTurnVisible('session:bran', ['interaction:2']);
    const completedDiagnostic = getRpgTurnDiagnostics('session:bran')[0];
    expect(completedDiagnostic.client.storeToCommitMs).toBeGreaterThanOrEqual(0);
    expect(completedDiagnostic.client.commitToVisibleMs).toBeGreaterThanOrEqual(0);
    expect(completedDiagnostic.client.requestToVisibleMs).toBeGreaterThanOrEqual(0);
  });

  it('discards optimistic entries when the client falls back from a missing foreground route', async () => {
    const fetchImpl = vi.fn(async () => new Response('not found', { status: 404 }));
    installRpgTurnUiFetchInterceptor(fetchImpl as typeof fetch);

    const response = await pipelineFetch('/api/rpg/sessions/session%3Abran/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'How is business?' }),
    });

    expect(response.status).toBe(404);
    expect(getRpgTurnUiEntries('session:bran')).toEqual([]);
  });

  it('does not request heavy query refreshes for explicit conversation-only turns', () => {
    expect(refreshPathsForChangedDomains('session:bran', ['conversation'])).toEqual([
      '/api/rpg/sessions/session%3Abran',
    ]);
    expect(refreshPathsForChangedDomains('session:bran', [])).toEqual([
      '/api/rpg/sessions/session%3Abran',
    ]);
    // Inventory lives in the session payload; no separate route is cached.
    expect(refreshPathsForChangedDomains('session:bran', ['conversation', 'inventory', 'currency'])).toEqual([
      '/api/rpg/sessions/session%3Abran',
    ]);
    expect(refreshPathsForChangedDomains('session:bran', ['location', 'world'])).toEqual([
      '/api/rpg/sessions/session%3Abran',
      '/api/reports',
    ]);
  });

  it('replaces a canonical base turn using interaction identity rather than text', () => {
    const merged = mergeRpgTurnUiMessages(
      [{
        id: 'interaction:1:response',
        interactionId: 'interaction:1',
        avatar: 'B',
        speaker: 'Bran',
        text: 'A combined persisted response.',
        tone: 'npc',
      }],
      [{
        id: 'interaction:1:message:0',
        sessionId: 'session:bran',
        submissionId: 'submit:one',
        interactionId: 'interaction:1',
        status: 'complete',
        avatar: 'B',
        speaker: 'Bran',
        text: 'A different incremental response.',
        tone: 'npc',
      }],
    );

    expect(merged).toHaveLength(1);
    expect(merged[0].text).toBe('A different incremental response.');
  });

  it('preserves identical wording from distinct interaction ids', () => {
    const merged = mergeRpgTurnUiMessages([], [
      {
        id: 'interaction:1:message:0',
        sessionId: 'session:bran',
        submissionId: 'submit:one',
        interactionId: 'interaction:1',
        status: 'complete',
        avatar: 'B',
        speaker: 'Bran',
        text: 'Steady enough.',
        tone: 'npc',
      },
      {
        id: 'interaction:2:message:0',
        sessionId: 'session:bran',
        submissionId: 'submit:two',
        interactionId: 'interaction:2',
        status: 'complete',
        avatar: 'B',
        speaker: 'Bran',
        text: 'Steady enough.',
        tone: 'npc',
      },
    ]);

    expect(merged).toHaveLength(2);
    expect(storyMessageIdentity(merged[0], 0)).toBe('interaction:1:message:0');
    expect(storyMessageIdentity(merged[1], 1)).toBe('interaction:2:message:0');
  });
});
