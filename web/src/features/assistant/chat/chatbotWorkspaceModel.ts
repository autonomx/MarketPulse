/** Types, settings, constants and helpers of the Chat workspace (WP-9.5). */
import { ApiError, type AssetListResponse, type ChatSession as ApiChatSession, type CodingApprovalPolicy, type JobRecord, type ProviderFacadePayload } from '../../../api/client';
import { fetchBytes, statusError } from '../../../api/transport';
import { createInMemoryAssistantWorkspaceEventStore, createStoredAssistantWorkspaceEventStore, type AssistantWorkspaceEvent, type AssistantWorkspaceEventStore, type AssistantWorkspaceEventStoreFilter, type AssistantWorkspaceEventStorage, type AssistantWorkspaceRuntimeConfig, type TtsSynthesisResponse, isLiveVoiceControllerInstalled, isLiveVoiceUnifiedAudioInstalled } from '../workspace';
import type { components } from '../api/generated';
import { chatStreamEventSchema, isFallbackOutputRef, jobOutputRefs, parseSseData } from '../../../api/schemas/streams';

export interface ChatbotFormValues {
  content: string;
  providerId: string;
  modelId: string;
  userTurnId?: string;
}

export type PastedChatImage = {
  dataUrl: string;
  mimeType: string;
  size: number;
};

export type PastedChatTextFile = {
  filename: string;
  mimeType: string;
  size: number;
  text: string;
};

export const MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024;
export const MAX_CHAT_TEXT_FILE_BYTES = 100 * 1024;
export const DEFAULT_IMAGE_MESSAGE = 'Please analyze the attached image.';
export const DEFAULT_IMAGES_MESSAGE = 'Please analyze the attached images.';
export const DEFAULT_TEXT_FILE_MESSAGE = 'Please analyze the attached file.';

export type AssistantView = 'chats' | 'live' | 'voice' | 'tools' | 'characters' | 'memory' | 'settings';
export type UtilityPanel = 'voice' | 'tools';
export type VoiceCaptureMode = 'idle' | 'listening' | 'recording' | 'transcribing' | 'error';
export type VoiceProfileAsset = AssetListResponse['assets'][number];
export type PersonalityId = 'default' | 'concise' | 'coach' | 'technical' | 'creative' | 'custom';
export type AssistantMessageFeedback = 'liked' | 'disliked';

export type ChatMessage = components['schemas']['ChatMessage'];

export type BrowserSpeechRecognitionAlternative = { transcript: string };
export type BrowserSpeechRecognitionResult = { isFinal: boolean; 0?: BrowserSpeechRecognitionAlternative };
export type BrowserSpeechRecognitionEvent = { resultIndex: number; results: { length: number; [index: number]: BrowserSpeechRecognitionResult } };
export type BrowserSpeechRecognitionErrorEvent = { error?: string; message?: string };
export type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};
export type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;
export type SpeechRecognitionWindow = Window & { SpeechRecognition?: BrowserSpeechRecognitionConstructor; webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor };

export type AssistantSettings = {
  voiceId: string;
  personalityId: PersonalityId;
  customPersonality: string;
  liveVoiceSensitivity: number;
  codingApprovalPolicy: CodingApprovalPolicy;
};

export const assistantSidebarItems: Array<{ id: AssistantView; label: string; icon: string }> = [
  { id: 'chats', label: 'Chats', icon: '▣' },
  { id: 'live', label: 'Live Chat', icon: '◉' },
  { id: 'voice', label: 'Voice Sessions', icon: '◉' },
  { id: 'tools', label: 'Tools', icon: '⚒' },
  { id: 'characters', label: 'Characters', icon: '♙' },
  { id: 'memory', label: 'Memory', icon: '▦' },
  { id: 'settings', label: 'Settings', icon: '⚙' },
];

export const suggestedPrompts = ['Tell me a fun fact', 'Recommend a movie', 'Give me productivity tips'] as const;
export const CALL_TIMER_TICK_MS = 1_000;
export const DEFAULT_SPEECH_LANGUAGE = 'en-US';
export const DEFAULT_LIVE_VOICE_SENSITIVITY = 55;
export const DEFAULT_CODING_APPROVAL_POLICY: CodingApprovalPolicy = 'ask_sensitive';
export const codingApprovalOptions: Array<{ value: CodingApprovalPolicy; label: string; description: string }> = [
  { value: 'always_ask', label: 'Ask for approval', description: 'Approve coding commands and file edits before they run.' },
  { value: 'ask_sensitive', label: 'Approve for me', description: 'Run safe coding actions automatically and ask only for higher-risk actions.' },
  { value: 'allow_automatic', label: 'Full access', description: 'Run workspace-scoped coding actions without approval prompts.' },
];
export const ASSISTANT_SETTINGS_STORAGE_KEY = 'omnix.chatbot.assistantSettings';
export const ASSISTANT_VIEW_STORAGE_KEY = 'omnix.chatbot.activeView';
export const ASSISTANT_SIDEBAR_STORAGE_KEY = 'omnix.chatbot.assistantSidebarMinimized';
export const ASSISTANT_SIDE_PANEL_STORAGE_KEY = 'omnix.chatbot.sidePanelMinimized';
export const STREAMING_TTS_SAMPLE_RATE = 24_000;
export const STREAMING_TTS_RECOVERY_DELAY_SECONDS = 0.05;
export const STREAMED_TTS_MIN_PHRASE_CHARS = 90;
export const LIVE_VOICE_AUTO_SEND_DELAY_MS = 600;
export const LIVE_SESSION_PROJECTION_FALLBACK_DELAY_MS = 0;
export const CHAT_JOB_TERMINAL_STATUSES = new Set(['completed', 'failed', 'canceled', 'stale']);

export function liveVoiceSubmissionKey(content: string): string {
  return content.trim().toLocaleLowerCase().replace(/[^\p{L}\p{N}']+/gu, ' ').trim();
}

export function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

export function dedicatedLiveVoiceControllerInstalled(): boolean {
  return isLiveVoiceControllerInstalled();
}

export function unifiedLiveVoiceAudioInstalled(): boolean {
  return isLiveVoiceUnifiedAudioInstalled();
}

export type VoicePerformanceStage = {
  stage?: unknown;
  turnId?: unknown;
  transcriptChars?: unknown;
  sttFinalizeMs?: unknown;
  delayMs?: unknown;
  pace?: unknown;
  probabilityDone?: unknown;
  reason?: unknown;
};

export type ChatStreamEvent = {
  type?: string;
  text?: string;
  message?: unknown;
  session?: ApiChatSession;
};

export type VoiceTurnPerformance = {
  turnId: string;
  sttFinalReceivedAt: number;
  transcriptChars?: number;
  sttFinalizeMs?: number;
  chatSubmitStartedAt?: number;
  chatResponseReceivedAt?: number;
  llmFirstChunkReceivedAt?: number;
  llmCompletedAt?: number;
  ttsStartedAt?: number;
  ttsReadyAt?: number;
  ttsFirstChunkReceivedAt?: number;
  audioFirstScheduledAt?: number;
  audioPlayStartedAt?: number;
  turnaroundLogged?: boolean;
};

export type VoiceTurnTimestampStage = Exclude<
  keyof VoiceTurnPerformance,
  'turnId' | 'sttFinalReceivedAt' | 'transcriptChars' | 'sttFinalizeMs' | 'turnaroundLogged'
>;

export type StreamingTtsPlayback = {
  audioContext: AudioContext;
  abortController: AbortController;
  sources: AudioBufferSourceNode[];
  closed: boolean;
};

export type StreamingTtsWindow = Window & typeof globalThis & {
  AudioContext?: typeof AudioContext;
  webkitAudioContext?: typeof AudioContext;
};

export const personalityOptions: Array<{ id: PersonalityId; label: string; prompt: string }> = [
  {
    id: 'default',
    label: 'Omnix Default',
    prompt: 'You are Omnix Assistant. Be helpful, clear, and practical.',
  },
  {
    id: 'concise',
    label: 'Concise operator',
    prompt: 'You are Omnix Assistant. Be direct, concise, and action-oriented. Prefer short answers unless detail is requested.',
  },
  {
    id: 'coach',
    label: 'Friendly coach',
    prompt: 'You are Omnix Assistant. Be warm, encouraging, and practical. Ask at most one clarifying question when needed.',
  },
  {
    id: 'technical',
    label: 'Technical expert',
    prompt: 'You are Omnix Assistant. Be precise, technical, and implementation-focused. Include concrete steps and caveats.',
  },
  {
    id: 'creative',
    label: 'Creative collaborator',
    prompt: 'You are Omnix Assistant. Be imaginative, collaborative, and vivid while staying useful and grounded.',
  },
  {
    id: 'custom',
    label: 'Custom personality',
    prompt: '',
  },
];


export function chatCapableProviders(payload: ProviderFacadePayload | undefined) { return payload?.providers.filter((provider) => provider.capabilities.includes('chat')) ?? []; }
export function chatCapableModels(payload: ProviderFacadePayload | undefined, providerId: string) { return payload?.models.filter((model) => { const providerMatches = providerId ? model.provider_id === providerId : true; return providerMatches && model.capabilities.includes('chat'); }) ?? []; }
export function selectedProviderLabel(payload: ProviderFacadePayload | undefined, providerId: string) { if (!providerId) return 'Default provider'; return payload?.providers.find((provider) => provider.id === providerId)?.label ?? providerId; }
export function selectedModelLabel(payload: ProviderFacadePayload | undefined, modelId: string) { if (!modelId) return 'Default model'; return payload?.models.find((model) => model.id === modelId)?.label ?? modelId; }
export function chatbotSubmitErrorMessage(error: unknown): string { if (error instanceof ApiError) return error.message; if (error instanceof Error) return error.message; return 'Chat request failed'; }
export function formatClockTime(value: string): string { return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
export function formatCallDuration(valueMs: number): string { const totalSeconds = Math.max(0, Math.floor(valueMs / 1000)); const hours = Math.floor(totalSeconds / 3600); const minutes = Math.floor((totalSeconds % 3600) / 60); const seconds = totalSeconds % 60; return [hours, minutes, seconds].map((value) => value.toString().padStart(2, '0')).join(':'); }
export function createChatbotWorkspaceEventStore(config: AssistantWorkspaceRuntimeConfig): AssistantWorkspaceEventStore { const storage = getAssistantWorkspaceEventStorage(); if (config.features.persistedEvents && storage) return createStoredAssistantWorkspaceEventStore(storage, config.eventStorageKey); return createInMemoryAssistantWorkspaceEventStore(); }
export function appendWorkspaceEventIfMissing(eventStore: AssistantWorkspaceEventStore, event: AssistantWorkspaceEvent, filter: AssistantWorkspaceEventStoreFilter): void { const currentEventIds = new Set(eventStore.list(filter).map((currentEvent) => currentEvent.id)); if (!currentEventIds.has(event.id)) eventStore.append(event); }
export function getAssistantWorkspaceEventStorage(): AssistantWorkspaceEventStorage | undefined { try { return typeof window === 'undefined' ? undefined : window.localStorage; } catch { return undefined; } }
export function createWorkspaceEventFilter(config: AssistantWorkspaceRuntimeConfig, sessionId?: string): AssistantWorkspaceEventStoreFilter { return { workspaceId: config.workspaceId, projectId: config.projectId, sessionId }; }
export function readAssistantToolReturn(): { message: string | null; toolId: string | null } { try { if (typeof window === 'undefined') return { message: null, toolId: null }; const params = new URLSearchParams(window.location.search); const toolId = params.get('assistant_tool'); return { message: params.get('assistant_tool_message'), toolId: toolId && /^[a-z][a-z0-9_-]*$/.test(toolId) ? toolId : null }; } catch { return { message: null, toolId: null }; } }
export function getLatestAssistantMessage(messages: ChatMessage[]): ChatMessage | undefined { return [...messages].reverse().find((message) => message.role === 'assistant' && message.content.trim()); }
export function isScrolledNearBottom(element: HTMLElement): boolean { return element.scrollHeight - element.scrollTop - element.clientHeight < 160; }
export function getSynthesizedAudioSource(response: TtsSynthesisResponse): string { if (response.audioUrl) return response.audioUrl; if (response.audioBase64) return `data:${response.mimeType ?? 'audio/wav'};base64,${response.audioBase64}`; throw new Error('TTS service did not return playable audio.'); }
/** The fields the session list reads; summaries and full sessions both have them. */
export type SessionListEntry = Pick<ApiChatSession, 'id' | 'title' | 'created_at' | 'updated_at'>;

export function mergeTranscript(current: string, next: string): string { return [current.trim(), next.trim()].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim(); }
export function shouldFlushStreamedSpeechBuffer(value: string): boolean { const text = value.trim(); if (text.length < STREAMED_TTS_MIN_PHRASE_CHARS) return false; return /[.!?]["')\]]?$/.test(text) || text.length >= STREAMED_TTS_MIN_PHRASE_CHARS * 2; }
export function elapsedMs(start: number | undefined, end: number | undefined): number | null { return start === undefined || end === undefined ? null : Math.round(end - start); }
export function voiceCaptureLabel(mode: VoiceCaptureMode): string { if (mode === 'recording') return 'Recording'; if (mode === 'transcribing') return 'Transcribing'; if (mode === 'error') return 'Error'; if (mode === 'listening') return 'Listening'; return 'Ready'; }
export function getSpeechRecognitionConstructor(): BrowserSpeechRecognitionConstructor | undefined { if (typeof window === 'undefined') return undefined; const speechWindow = window as SpeechRecognitionWindow; return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition; }
export function canUseDecodedAudioPlayback(): boolean { if (typeof window === 'undefined') return false; const liveWindow = window as StreamingTtsWindow; return Boolean(liveWindow.AudioContext || liveWindow.webkitAudioContext); }
// Checked at the boundary; the session is then read as the full ChatSession the route sends.
export function parseChatStreamEvent(value: string): ChatStreamEvent | null { return parseSseData(chatStreamEventSchema, value) as ChatStreamEvent | null; }
export function makePlayableAudioSource(source: string): { url: string; revoke?: () => void } { if (!source.startsWith('data:audio/') || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return { url: source }; const blob = dataUrlToBlob(source); const url = URL.createObjectURL(blob); return { url, revoke: () => URL.revokeObjectURL(url) }; }
export function dataUrlToBlob(source: string): Blob { const [header, encoded = ''] = source.split(',', 2); const mime = /^data:([^;,]+)/.exec(header)?.[1] || 'audio/wav'; const binary = window.atob(encoded); const bytes = new Uint8Array(binary.length); for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index); return new Blob([bytes], { type: mime }); }
export async function audioSourceToArrayBuffer(source: string): Promise<ArrayBuffer> { if (source.startsWith('data:')) return dataUrlToArrayBuffer(source); const response = await fetchBytes(source).catch(statusError('Audio fetch')); return response.arrayBuffer(); }
export function dataUrlToArrayBuffer(source: string): ArrayBuffer { const [, encoded = ''] = source.split(',', 2); return base64ToArrayBuffer(encoded); }
export function waitForAudioElementPlaying(audio: HTMLAudioElement): Promise<void> { return new Promise((resolve, reject) => { if (typeof audio.addEventListener !== 'function') { resolve(); return; } if (!audio.paused && audio.readyState >= 3) { resolve(); return; } let timeoutId: ReturnType<typeof setTimeout> | null = null; const cleanup = () => { audio.removeEventListener('playing', onPlaying); audio.removeEventListener('error', onError); if (timeoutId !== null) clearTimeout(timeoutId); }; const onPlaying = () => { cleanup(); resolve(); }; const onError = () => { cleanup(); reject(new Error(audio.error?.message || 'Audio playback failed before it started.')); }; audio.addEventListener('playing', onPlaying, { once: true }); audio.addEventListener('error', onError, { once: true }); timeoutId = setTimeout(() => { cleanup(); reject(new Error('Audio element did not start playing within 3s.')); }, 3000); }); }
export function waitForAudioElementToFinish(audio: HTMLAudioElement): Promise<void> { return new Promise((resolve) => { if (audio.ended || audio.paused || typeof audio.addEventListener !== 'function') { resolve(); return; } const done = () => resolve(); audio.addEventListener('ended', done, { once: true }); audio.addEventListener('pause', done, { once: true }); audio.addEventListener('error', done, { once: true }); }); }
export function waitForStreamingPlaybackToFinish(playback: StreamingTtsPlayback, isCancelled: () => boolean): Promise<void> { return new Promise((resolve) => { const tick = () => { if (playback.closed || playback.sources.length === 0 || isCancelled()) { resolve(); return; } window.setTimeout(tick, 25); }; tick(); }); }
export function base64ToArrayBuffer(value: string): ArrayBuffer { const binary = window.atob(value); const bytes = new Uint8Array(binary.length); for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index); return bytes.buffer; }
export function getVoiceJobAudioSource(job: JobRecord): string | null { for (const output of jobOutputRefs(job)) { if (isFallbackOutputRef(output)) continue; if (typeof output.data_url === 'string' && output.data_url.startsWith('data:audio/')) return output.data_url; if (typeof output.audio_url === 'string' && output.audio_url.trim()) return output.audio_url; } return null; }
export function voiceJobErrorMessage(job: JobRecord): string { if (job.status !== 'failed') return ''; const error = job.error as { message?: unknown } | null | undefined; return typeof error?.message === 'string' ? error.message : 'Voice Studio TTS job failed.'; }
export function getVoiceProfileAssets(payload: AssetListResponse | undefined): VoiceProfileAsset[] { return payload?.assets.filter((asset) => asset.type === 'voice_profile') ?? []; }
export function asRecord(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
export function voiceProfileId(asset: VoiceProfileAsset): string { const metadata = asRecord(asset.metadata); return stringMetadata(metadata.voice_id) || stringMetadata(metadata.profile_id) || stringMetadata(metadata.id) || asset.id; }
export function voiceProfileLabel(asset: VoiceProfileAsset): string { const metadata = asRecord(asset.metadata); return stringMetadata(metadata.profile_name) || stringMetadata(metadata.name) || stringMetadata(metadata.voice_name) || asset.file_name || asset.id; }
export function voiceLabelForId(voiceId: string, voiceProfiles: VoiceProfileAsset[]): string { if (!voiceId) return ''; const profile = voiceProfiles.find((asset) => voiceProfileId(asset) === voiceId || asset.id === voiceId); return profile ? voiceProfileLabel(profile) : voiceId; }
export function stringMetadata(value: unknown): string { return typeof value === 'string' ? value.trim() : ''; }
export function readFileAsDataUrl(file: File): Promise<string> { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('Image data was not text.')); reader.onerror = () => reject(reader.error ?? new Error('Image read failed.')); reader.readAsDataURL(file); }); }
export function attachmentDefaultMessage(images: PastedChatImage[], textFile: PastedChatTextFile | null): string { return images.length > 1 ? DEFAULT_IMAGES_MESSAGE : images.length === 1 ? DEFAULT_IMAGE_MESSAGE : textFile ? DEFAULT_TEXT_FILE_MESSAGE : ''; }
export async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
    if (typeof document === 'undefined') return false;
    // Without the Clipboard API (plain-HTTP LAN origins) only a selected, attached textarea can be copied.
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', 'true');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    // eslint-disable-next-line no-restricted-syntax -- execCommand('copy') needs the textarea in the document
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    return copied;
  } catch {
    return false;
  }
}
export function defaultAssistantSettings(config: AssistantWorkspaceRuntimeConfig): AssistantSettings { return { voiceId: config.ttsVoice ?? '', personalityId: 'default', customPersonality: '', liveVoiceSensitivity: DEFAULT_LIVE_VOICE_SENSITIVITY, codingApprovalPolicy: DEFAULT_CODING_APPROVAL_POLICY }; }
export function loadAssistantSettings(config: AssistantWorkspaceRuntimeConfig): AssistantSettings { const fallback = defaultAssistantSettings(config); try { if (typeof window === 'undefined') return fallback; const raw = window.localStorage.getItem(ASSISTANT_SETTINGS_STORAGE_KEY); if (!raw) return fallback; const parsed = JSON.parse(raw) as Partial<AssistantSettings>; return { voiceId: typeof parsed.voiceId === 'string' ? parsed.voiceId : fallback.voiceId, personalityId: isPersonalityId(parsed.personalityId) ? parsed.personalityId : fallback.personalityId, customPersonality: typeof parsed.customPersonality === 'string' ? parsed.customPersonality : fallback.customPersonality, liveVoiceSensitivity: clampLiveVoiceSensitivity(parsed.liveVoiceSensitivity), codingApprovalPolicy: isCodingApprovalPolicy(parsed.codingApprovalPolicy) ? parsed.codingApprovalPolicy : fallback.codingApprovalPolicy }; } catch { return fallback; } }
export function saveAssistantSettings(settings: AssistantSettings): void { try { if (typeof window !== 'undefined') window.localStorage.setItem(ASSISTANT_SETTINGS_STORAGE_KEY, JSON.stringify(settings)); } catch { /* ignore local storage failures */ } }
export function clampLiveVoiceSensitivity(value: unknown): number { const parsed = typeof value === 'number' ? value : Number(value); if (!Number.isFinite(parsed)) return DEFAULT_LIVE_VOICE_SENSITIVITY; return Math.min(100, Math.max(1, Math.round(parsed))); }
export function isCodingApprovalPolicy(value: unknown): value is CodingApprovalPolicy { return value === 'always_ask' || value === 'ask_sensitive' || value === 'allow_automatic'; }
export function isPersonalityId(value: unknown): value is PersonalityId { return typeof value === 'string' && personalityOptions.some((option) => option.id === value); }
export function personalityLabel(value: PersonalityId): string { return personalityOptions.find((option) => option.id === value)?.label ?? 'Omnix Default'; }
export function createPersonalityPrompt(settings: AssistantSettings): string | undefined { if (settings.personalityId === 'custom') return settings.customPersonality.trim() || undefined; return personalityOptions.find((option) => option.id === settings.personalityId)?.prompt; }
