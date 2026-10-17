import { useLayoutEffect, useRef, type RefObject } from 'react';
import { useWatch, type Control } from 'react-hook-form';
import {
  DesktopCompanionControls,
  DesktopCompanionTextSurface,
  DesktopShareStatusRow,
  LIVE_TASK_PRESETS,
  liveCallPresentationStore,
  liveCallVoiceMode,
  liveCaptureLabels,
  useLiveCallPresentation,
  useLiveVoiceTranscript,
} from '../workspace';
import type { CharacterLiveCallRuntime } from './characterClient';
import { formatMessageTime } from './chatMessageModel';
import { formatCallDuration, formatClockTime, voiceCaptureLabel, type ChatMessage, type ChatbotFormValues, type VoiceCaptureMode } from './chatbotWorkspaceModel';
import { enterLiveChatFullscreen } from './live-chat-fullscreen-controller';
import { Live2DMotionControl } from './Live2DMotionControl';
import { Live2DZoomControl } from './Live2DZoomControl';
import { LiveCallVisual } from './LiveCallVisual';

type ChatLiveVoiceCardProps = {
  /** The capture controller binds its microphone stream to this card. */
  cardRef: RefObject<HTMLElement | null>;
  voiceId: string;
  liveCallRuntime: CharacterLiveCallRuntime | null;
  identityLabel: string;
  liveVoiceActive: boolean;
  voiceCaptureMode: VoiceCaptureMode;
  isAssistantSpeaking: boolean;
  callElapsedMs: number;
  thinking: boolean;
  control: Control<ChatbotFormValues>;
  liveDraftText: string;
  sendPending: boolean;
  transcriptMessages: ChatMessage[];
  autoSpeakResponses: boolean;
  speechInputLabel: string;
  ttsOutputLabel: string;
  onToggleCall: () => void;
  onClear: () => void;
  onSendVoiceText: () => void;
  onAutoSpeakChange: (autoSpeak: boolean) => void;
  onTestInput: () => void;
};

/** The Live Voice card: call state, the avatar, controls, the transcript and audio services. */
export function ChatLiveVoiceCard({
  cardRef,
  voiceId,
  liveCallRuntime,
  identityLabel: liveIdentityLabel,
  liveVoiceActive,
  voiceCaptureMode,
  isAssistantSpeaking,
  callElapsedMs,
  thinking,
  control,
  liveDraftText,
  sendPending,
  transcriptMessages,
  autoSpeakResponses,
  speechInputLabel,
  ttsOutputLabel,
  onToggleCall,
  onClear,
  onSendVoiceText,
  onAutoSpeakChange,
  onTestInput,
}: ChatLiveVoiceCardProps) {
  const livePresentation = useLiveCallPresentation();
  const liveVoiceTranscript = useLiveVoiceTranscript();
  // With the capture controller installed, the card shows its microphone stream.
  const captureLabels = livePresentation.captureOwned ? liveCaptureLabels(livePresentation.captureStatus) : null;
  const liveVoiceState = captureLabels ? captureLabels.state : liveVoiceActive ? voiceCaptureLabel(voiceCaptureMode) : voiceCaptureMode === 'error' ? 'Error' : 'Idle';
  const liveConnectionLabel = captureLabels ? captureLabels.connection : liveVoiceActive ? 'Connected' : 'Disconnected';
  const liveInputLabel = captureLabels ? (livePresentation.hearing ? 'Hearing you' : captureLabels.input) : liveVoiceActive ? 'Listening' : 'Idle';
  const liveVoiceInputMode = livePresentation.captureOwned
    ? livePresentation.hearing ? 'active' : livePresentation.captureStatus === 'connected' ? 'listening' : livePresentation.captureStatus
    : undefined;
  const liveCallButtonActive = livePresentation.captureOwned ? livePresentation.captureActive : liveVoiceActive;
  const liveCallTimerLabel = formatCallDuration(callElapsedMs);
  const liveVoiceVisualMode = isAssistantSpeaking ? 'speaking'
    : livePresentation.captureOwned || livePresentation.speaking ? liveCallVoiceMode(livePresentation, liveVoiceActive)
      : liveVoiceActive ? 'listening' : voiceCaptureMode === 'error' ? 'error' : 'idle';
  const voiceTranscriptRef = useRef<HTMLDivElement | null>(null);
  const lastVoiceTranscriptMessageId = transcriptMessages.at(-1)?.id;
  // The newest words stay in view as the transcript grows.
  useLayoutEffect(() => {
    const transcript = voiceTranscriptRef.current;
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }, [lastVoiceTranscriptMessageId, liveVoiceTranscript]);

  return (
    <section className="assistant-live-card" ref={cardRef} data-live-voice-id={voiceId} data-live-voice-status={livePresentation.captureOwned ? livePresentation.captureStatus : undefined} data-voice-input={liveVoiceInputMode} data-live-voice-output-kind={livePresentation.outputKind ?? undefined}>
      <header><div><p className="eyebrow">Live Voice</p><span className={liveCallRuntime?.interaction_mode === 'character' ? 'assistant-live-identity active' : 'assistant-live-identity'}>{liveIdentityLabel}</span></div><div className="assistant-live-header-actions"><strong>{liveConnectionLabel}</strong><button type="button" className="assistant-live-fullscreen-button" aria-label="Enter fullscreen Live Voice" onClick={() => enterLiveChatFullscreen('call-card')}>Fullscreen</button></div>{livePresentation.captureOwned ? <select aria-label="Live task" data-live-task-instruction="true" value={livePresentation.taskInstruction} onChange={(event) => liveCallPresentationStore.setTaskInstruction(event.currentTarget.value)}>{LIVE_TASK_PRESETS.map((preset) => <option key={preset.label} value={preset.value}>{preset.label}</option>)}</select> : null}</header>
      {liveCallRuntime?.avatar_pack?.renderer === 'live2d'
        ? <Live2DMotionControl rigAssetId={liveCallRuntime.avatar_pack.rig_asset_id} />
        : <div className="assistant-live-state" role="status" aria-label="Live voice state"><span>{liveVoiceState}</span><span aria-hidden="true">v</span></div>}
      <LiveCallVisual voiceMode={liveVoiceVisualMode} thinking={thinking} />
      {liveCallRuntime?.avatar_pack?.renderer === 'live2d' ? <Live2DZoomControl /> : null}
      <div className="assistant-voice-input-indicator" aria-live="polite">
        <span>Mic input</span>
        <strong className="assistant-voice-input-status">{liveInputLabel}</strong>
        <i aria-hidden="true"><b /></i>
      </div>
      <time className="assistant-call-timer" dateTime={`PT${Math.floor(callElapsedMs / 1000)}S`}>{liveCallTimerLabel}</time>
      <div className="assistant-voice-controls" role="group" aria-label="Live voice controls"><button type="button" onClick={onClear}>Clear</button><button type="button" className={liveCallButtonActive ? 'danger' : undefined} disabled={livePresentation.captureOwned && livePresentation.captureStatus === 'connecting'} onClick={onToggleCall}>{liveCallButtonActive ? 'End Call' : 'Start Call'}</button><SendVoiceTextButton control={control} draft={liveDraftText} pending={sendPending} onSend={onSendVoiceText} /></div>
      <div className="assistant-voice-transcript" ref={voiceTranscriptRef} role="region" aria-label="Live voice transcript"><div className="assistant-voice-transcript-header"><h3>Transcript</h3><button type="button" onClick={onClear}>Clear</button></div>{transcriptMessages.map((message) => <p key={`transcript-${message.id}`} className={message.role === 'assistant' ? 'assistant' : 'user'}><span><strong>{message.role === 'assistant' ? 'Omnix' : 'You'}</strong><time dateTime={message.created_at}>{formatMessageTime(message.created_at)}</time></span>{message.content}</p>)}{liveVoiceTranscript.rows.map((row) => <p key={row.id} className={row.speaker === 'Omnix' ? 'assistant' : 'user'} data-live-voice-id={row.draft ? 'live-voice-draft' : row.id}><span><strong>{row.speaker}</strong><time dateTime={row.at}>{formatClockTime(row.at)}</time></span>{row.text}</p>)}{liveVoiceTranscript.delivery ? <p className="assistant" data-omnix-live-delivery="true">{`Assistant: ${liveVoiceTranscript.delivery.text}${liveVoiceTranscript.delivery.partial ? ' [partial]' : ''}`}</p> : null}{!transcriptMessages.length && !liveVoiceTranscript.rows.length && !liveVoiceTranscript.delivery ? <p className="muted">Voice transcript will appear here during live calls.</p> : null}</div>
      <label className="assistant-voice-toggle"><input type="checkbox" checked={autoSpeakResponses} onChange={(event) => onAutoSpeakChange(event.currentTarget.checked)} /> Auto-speak assistant replies</label>
      <div className="assistant-live-draft" aria-live="polite"><strong>Voice draft</strong><p>{liveDraftText || 'Start Live Voice and speak. Final speech is copied into the message composer.'}</p></div>
      <div className="assistant-audio-devices"><header><h3>Audio Services</h3><button type="button" onClick={onTestInput}>Test input</button></header><div><span>Input</span><strong>{speechInputLabel}</strong><i aria-hidden="true" /></div><div><span>Output</span><strong>{ttsOutputLabel}</strong><i aria-hidden="true" /></div><DesktopShareStatusRow /><DesktopCompanionControls /><DesktopCompanionTextSurface /></div>
      <footer className="assistant-voice-status"><span>Voice Status</span><strong>{liveVoiceState}</strong></footer>
    </section>
  );
}

// Watches the composer here, so typing re-renders this button rather than all of Chat.
function SendVoiceTextButton({ control, draft, pending, onSend }: { control: Control<ChatbotFormValues>; draft: string; pending: boolean; onSend: () => void }) {
  const content = useWatch({ control, name: 'content' }) ?? '';
  return <button type="button" onClick={onSend} disabled={pending || !(draft || content).trim()}>Send text</button>;
}

