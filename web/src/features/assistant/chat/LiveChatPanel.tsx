 
import { useEffect, useState } from 'react';

import {
  liveConversationStore,
  selectLiveChatSnapshot,
  useLiveConversationState,
} from '../workspace/live-conversation-store';
import { CharacterModePanel } from './CharacterModePanel';
import {
  enterLiveChatFullscreen,
  exitLiveChatFullscreen,
  initializeLiveChatFullscreenController,
  useLiveChatFullscreenState,
} from './live-chat-fullscreen-controller';
import { LiveConversationControls } from './LiveConversationControls';
import { LiveConversationEvaluationPanel } from './LiveConversationEvaluationPanel';
import { LivePronunciationPanel } from './LivePronunciationPanel';
import { LiveVoiceCalibrationPanel } from './LiveVoiceCalibrationPanel';
import './LiveChatPanel.css';
import './LiveChatFullscreenEntry.css';

export type LiveChatPanelProps = {
  sessionId: string | null;
  onSessionResolved?: (sessionId: string) => void;
  /** Starts or ends the live call (Chat's call controls). */
  onToggleCall: () => void;
};

export function LiveChatPanel({ sessionId, onSessionResolved, onToggleCall }: LiveChatPanelProps) {
  const runtimeState = useLiveConversationState();
  const snapshot = selectLiveChatSnapshot(runtimeState);
  const fullscreen = useLiveChatFullscreenState();
  const [callStatus, setCallStatus] = useState<string | null>(null);

  useEffect(() => {
    liveConversationStore.dispatch({ type: 'session', sessionId });
  }, [sessionId]);

  useEffect(() => {
    const dispose = initializeLiveChatFullscreenController();
    return () => {
      dispose();
      void exitLiveChatFullscreen();
    };
  }, []);

  function toggleCall(): void {
    onToggleCall();
    setCallStatus(snapshot.connected ? 'Ending live call…' : 'Starting live call…');
  }

  const duplexLabel = snapshot.duplexMode === 'echo_aware'
    ? 'Echo-aware barge-in'
    : snapshot.duplexReason === 'calibration_missing'
      ? 'Safe half-duplex · calibration required'
      : 'Safe half-duplex';

  return (
    <section className="assistant-view-panel live-chat-panel" aria-label="Live Chat view">
      <header className="live-chat-page-header">
        <div>
          <p className="eyebrow">Omnix Assistant</p>
          <h2>Live Chat</h2>
          <p>Configure the character and conversation presence used by the existing live voice pipeline.</p>
        </div>
        <div className="live-chat-page-actions">
          <span className={snapshot.connected ? 'live-chat-status active' : 'live-chat-status'}>
            {snapshot.connected ? 'Call connected' : snapshot.connection === 'connecting' ? 'Call connecting' : 'Call idle'}
          </span>
          <button
            type="button"
            className="live-chat-fullscreen-action"
            aria-pressed={fullscreen.immersive}
            onClick={() => enterLiveChatFullscreen('header')}
          >
            Enter fullscreen
          </button>
        </div>
      </header>

      {!sessionId ? (
        <article className="live-chat-card live-chat-empty" role="status">
          <h3>Select a Chat session</h3>
          <p>Choose or create a Chat session to configure its character. Presence changes below update your user defaults.</p>
        </article>
      ) : (
        <CharacterModePanel sessionId={sessionId} onSessionResolved={onSessionResolved} />
      )}

      <LiveConversationControls sessionId={sessionId} />
      <LiveVoiceCalibrationPanel />
      <LivePronunciationPanel sessionId={sessionId} />

      <section className="live-chat-card" aria-labelledby="live-chat-call-heading">
        <header>
          <div>
            <p className="eyebrow">Call preview</p>
            <h3 id="live-chat-call-heading">Live conversation</h3>
            <p>This page controls the same microphone, STT, LLM, and PCM session used by Chats.</p>
          </div>
          <div className="live-chat-call-actions">
            <button
              type="button"
              className="live-chat-secondary-action"
              aria-pressed={fullscreen.immersive}
              onClick={() => enterLiveChatFullscreen('call-card')}
            >
              Fullscreen
            </button>
            <button type="button" onClick={toggleCall}>{snapshot.connected ? 'End Call' : 'Start Call'}</button>
          </div>
        </header>
        <dl className="live-chat-metrics">
          <div><dt>Identity</dt><dd>{snapshot.identity}</dd></div>
          <div><dt>State</dt><dd>{snapshot.state}</dd></div>
          <div><dt>Floor</dt><dd>{snapshot.floorOwner}</dd></div>
          <div><dt>Duplex</dt><dd>{duplexLabel}</dd></div>
        </dl>
        {callStatus ? <p className="live-chat-note" role="status">{callStatus}</p> : null}
      </section>

      <LiveConversationEvaluationPanel />
    </section>
  );
}
