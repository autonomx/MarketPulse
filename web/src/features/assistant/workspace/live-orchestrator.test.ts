import { describe, expect, it, vi } from 'vitest';
import { createInMemoryAssistantWorkspaceEventStore } from './event-store';
import { createStaticModelProvider } from './provider';
import { runLiveAssistantTurn } from './live-orchestrator';
import type { SttServiceClient, TtsServiceClient } from './speech-services';

describe('runLiveAssistantTurn', () => {
  it('runs transcription, model response, synthesis, and playback queueing in order', async () => {
    const stt: SttServiceClient = {
      transcribeAudio: vi.fn(async () => ({ text: 'What changed overnight?' })),
    };
    const tts: TtsServiceClient = {
      synthesizeSpeech: vi.fn(async () => ({ audioUrl: 'blob:reply', mimeType: 'audio/wav' })),
    };
    const provider = createStaticModelProvider('local', 'Local model', { streaming: false }, async (request) => ({
      content: [{ kind: 'text', text: `Answering: ${request.messages.at(-1)?.content[0]?.text}` }],
      finishReason: 'stop',
    }));

    const result = await runLiveAssistantTurn({
      sessionId: 'session:voice',
      audio: new ArrayBuffer(4),
      provider,
      model: 'qwen-local',
      stt,
      tts,
      systemPrompt: 'Keep it concise.',
      voice: 'narrator',
      now: () => '2026-06-23T09:00:00Z',
    });

    expect(stt.transcribeAudio).toHaveBeenCalledOnce();
    expect(result.modelRequest.messages[0]).toMatchObject({ role: 'system' });
    expect(result.modelRequest.messages[1]).toMatchObject({ role: 'user' });
    expect(tts.synthesizeSpeech).toHaveBeenCalledWith({
      text: 'Answering: What changed overnight?',
      voice: 'narrator',
      format: 'wav',
    });
    expect(result.playbackItem).toEqual({
      id: 'playback:session:voice:2026-06-23T09:00:00Z',
      text: 'Answering: What changed overnight?',
      createdAt: '2026-06-23T09:00:00Z',
    });
    expect(result.stages).toEqual(['transcribed', 'responded', 'synthesized', 'queued']);
  });

  it('records replayable live turn events when an event store is provided', async () => {
    const eventStore = createInMemoryAssistantWorkspaceEventStore();
    const stt: SttServiceClient = {
      transcribeAudio: vi.fn(async () => ({ text: 'Summarize the project state.' })),
    };
    const tts: TtsServiceClient = {
      synthesizeSpeech: vi.fn(async () => ({ audioUrl: 'blob:summary', mimeType: 'audio/wav' })),
    };
    const provider = createStaticModelProvider('local', 'Local model', { streaming: false }, async () => ({
      content: [{ kind: 'text', text: 'The workspace is ready.' }],
      finishReason: 'stop',
      tokenUsage: { inputTokens: 5, outputTokens: 5, totalTokens: 10 },
    }));
    const timestamps = [
      '2026-06-23T09:00:00Z',
      '2026-06-23T09:00:01Z',
      '2026-06-23T09:00:02Z',
      '2026-06-23T09:00:03Z',
      '2026-06-23T09:00:04Z',
    ];

    const result = await runLiveAssistantTurn({
      sessionId: 'session:voice',
      workspaceId: 'workspace:main',
      projectId: 'project:omnix',
      audio: new ArrayBuffer(4),
      provider,
      model: 'qwen-local',
      stt,
      tts,
      systemPrompt: 'Keep it concise.',
      eventStore,
      now: () => timestamps.shift() ?? '2026-06-23T09:00:05Z',
    });

    expect(result.events.map((event) => event.type)).toEqual([
      'voice_transcript',
      'user_message',
      'context_assembled',
      'assistant_message',
    ]);
    expect(eventStore.list({ sessionId: 'session:voice' }).map((event) => event.type)).toEqual([
      'voice_transcript',
      'user_message',
      'context_assembled',
      'assistant_message',
    ]);
    expect(eventStore.list({ type: 'assistant_message' })[0]).toMatchObject({
      workspaceId: 'workspace:main',
      projectId: 'project:omnix',
      payload: {
        turn: {
          role: 'assistant',
          content: [{ kind: 'text', text: 'The workspace is ready.' }],
          metadata: {
            provider: 'local',
            model: 'qwen-local',
            tokenUsage: { inputTokens: 5, outputTokens: 5, totalTokens: 10 },
          },
        },
      },
    });
  });
});
