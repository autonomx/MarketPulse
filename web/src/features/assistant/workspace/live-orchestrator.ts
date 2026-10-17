/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { createConversationTurn, type MessageContent } from './conversation';
import type { AssistantWorkspaceEventStore } from './event-store';
import type { AssistantWorkspaceEvent } from './events';
import type { ModelProvider, ModelRequest, ModelResponse } from './provider';
import type { PlaybackItem } from './playback';
import type { SpeechAudioInput, SttServiceClient, SttTranscriptionResponse, TtsServiceClient, TtsSynthesisResponse } from './speech-services';

export type LiveAssistantTurnInput = {
  sessionId: string;
  audio: SpeechAudioInput;
  provider: ModelProvider;
  model: string;
  stt: SttServiceClient;
  tts: TtsServiceClient;
  systemPrompt?: string;
  voice?: string;
  now?: () => string;
  eventStore?: AssistantWorkspaceEventStore;
  workspaceId?: string;
  projectId?: string;
};

export type LiveAssistantTurnResult = {
  sessionId: string;
  transcript: SttTranscriptionResponse;
  modelRequest: ModelRequest;
  modelResponse: ModelResponse;
  assistantText: string;
  synthesis: TtsSynthesisResponse;
  playbackItem: PlaybackItem;
  stages: Array<'transcribed' | 'responded' | 'synthesized' | 'queued'>;
  events: AssistantWorkspaceEvent[];
};

export async function runLiveAssistantTurn(input: LiveAssistantTurnInput): Promise<LiveAssistantTurnResult> {
  const now = input.now ?? (() => new Date().toISOString());
  const eventRecorder = createLiveTurnEventRecorder(input, now);
  const transcript = await input.stt.transcribeAudio({ audio: input.audio });
  const transcriptCreatedAt = now();

  eventRecorder.record({
    id: createLiveTurnEventId(input.sessionId, 'voice_transcript', transcriptCreatedAt),
    type: 'voice_transcript',
    workspaceId: eventRecorder.workspaceId,
    projectId: input.projectId,
    sessionId: input.sessionId,
    payload: {
      segmentId: `segment:${input.sessionId}:${transcriptCreatedAt}`,
      text: transcript.text,
      isFinal: true,
    },
    createdAt: transcriptCreatedAt,
  });

  const userTurnCreatedAt = now();
  const userTurn = createConversationTurn({
    id: `turn:${input.sessionId}:user:${userTurnCreatedAt}`,
    sessionId: input.sessionId,
    role: 'user',
    content: [{ kind: 'text', text: transcript.text }],
    metadata: { voiceSessionId: input.sessionId },
    createdAt: userTurnCreatedAt,
  });

  eventRecorder.record({
    id: createLiveTurnEventId(input.sessionId, 'user_message', userTurnCreatedAt),
    type: 'user_message',
    workspaceId: eventRecorder.workspaceId,
    projectId: input.projectId,
    sessionId: input.sessionId,
    payload: { turn: userTurn },
    createdAt: userTurnCreatedAt,
  });

  const messages = [
    ...(input.systemPrompt
      ? [
          {
            role: 'system' as const,
            content: [{ kind: 'text' as const, text: input.systemPrompt }],
          },
        ]
      : []),
    {
      role: 'user' as const,
      content: [{ kind: 'text' as const, text: transcript.text }],
    },
  ];
  const modelRequest: ModelRequest = {
    provider: input.provider.id,
    model: input.model,
    messages,
    metadata: {
      sessionId: input.sessionId,
      inputMode: 'voice',
    },
  };

  const contextCreatedAt = now();
  eventRecorder.record({
    id: createLiveTurnEventId(input.sessionId, 'context_assembled', contextCreatedAt),
    type: 'context_assembled',
    workspaceId: eventRecorder.workspaceId,
    projectId: input.projectId,
    sessionId: input.sessionId,
    payload: {
      sourceIds: input.systemPrompt ? ['system-prompt', userTurn.id] : [userTurn.id],
      estimatedTokens: estimateTextTokens([input.systemPrompt, transcript.text].filter(Boolean)),
    },
    createdAt: contextCreatedAt,
  });

  const modelResponse = await input.provider.execute(modelRequest);
  const assistantText = flattenModelResponseText(modelResponse.content);
  const responseCreatedAt = now();
  const assistantTurn = createConversationTurn({
    id: `turn:${input.sessionId}:assistant:${responseCreatedAt}`,
    sessionId: input.sessionId,
    role: 'assistant',
    content: modelResponse.content,
    metadata: {
      provider: input.provider.id,
      model: input.model,
      latencyMs: modelResponse.latencyMs,
      tokenUsage: modelResponse.tokenUsage,
      voiceSessionId: input.sessionId,
    },
    createdAt: responseCreatedAt,
  });

  eventRecorder.record({
    id: createLiveTurnEventId(input.sessionId, 'assistant_message', responseCreatedAt),
    type: 'assistant_message',
    workspaceId: eventRecorder.workspaceId,
    projectId: input.projectId,
    sessionId: input.sessionId,
    payload: { turn: assistantTurn },
    createdAt: responseCreatedAt,
  });

  const synthesis = await input.tts.synthesizeSpeech({
    text: assistantText,
    voice: input.voice,
    format: 'wav',
  });
  const createdAt = now();
  const playbackItem: PlaybackItem = {
    id: `playback:${input.sessionId}:${createdAt}`,
    text: assistantText,
    createdAt,
  };

  return {
    sessionId: input.sessionId,
    transcript,
    modelRequest,
    modelResponse,
    assistantText,
    synthesis,
    playbackItem,
    stages: ['transcribed', 'responded', 'synthesized', 'queued'],
    events: eventRecorder.events,
  };
}

export function flattenModelResponseText(content: MessageContent[]): string {
  return content
    .map((item) => item.text.trim())
    .filter(Boolean)
    .join('\n')
    .trim();
}

type LiveTurnEventRecorder = {
  workspaceId: string;
  events: AssistantWorkspaceEvent[];
  record(event: AssistantWorkspaceEvent): void;
};

function createLiveTurnEventRecorder(input: LiveAssistantTurnInput, now: () => string): LiveTurnEventRecorder {
  const workspaceId = input.workspaceId ?? 'workspace:default';
  const events: AssistantWorkspaceEvent[] = [];

  return {
    workspaceId,
    events,
    record(event) {
      events.push(input.eventStore?.append(event) ?? event);
    },
  };
}

function createLiveTurnEventId(sessionId: string, type: AssistantWorkspaceEvent['type'], createdAt: string): string {
  return `event:${sessionId}:${type}:${createdAt}`;
}

function estimateTextTokens(texts: Array<string | undefined>): number {
  return texts
    .join(' ')
    .split(/\s+/)
    .filter(Boolean).length;
}
