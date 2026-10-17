 
import { useEffect, useState } from 'react';

import { readLiveConversationEvaluationSnapshot, recordLiveConversationSurvey, resetLiveConversationEvaluation } from '../workspace/live-conversation-evaluation-controller';
import type { LiveConversationEvaluationReport } from '../workspace/live-conversation-evaluation';
import { LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT } from '../../../events/bus';

export function LiveConversationEvaluationPanel() {
  const [report, setReport] = useState<LiveConversationEvaluationReport>(
    () => readLiveConversationEvaluationSnapshot().report,
  );
  const [listeningScore, setListeningScore] = useState(4);
  const [pressureScore, setPressureScore] = useState(2);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    const refresh = (event?: Event) => {
      const detail = (event as CustomEvent<{ report?: LiveConversationEvaluationReport }> | undefined)?.detail;
      setReport(detail?.report ?? readLiveConversationEvaluationSnapshot().report);
    };
    window.addEventListener(LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT, refresh);
    refresh();
    return () => window.removeEventListener(LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT, refresh);
  }, []);

  function saveSurvey(): void {
    const snapshot = recordLiveConversationSurvey(listeningScore, pressureScore);
    setReport(snapshot.report);
    setStatus('Conversation experience score saved for this evaluation.');
  }

  function reset(): void {
    const snapshot = resetLiveConversationEvaluation();
    setReport(snapshot.report);
    setStatus('Live conversation evaluation reset.');
  }

  return (
    <section className="live-chat-card live-chat-evaluation" aria-labelledby="live-chat-evaluation-heading">
      <header>
        <div>
          <p className="eyebrow">Human-context evaluation</p>
          <h3 id="live-chat-evaluation-heading">Conversation quality</h3>
          <p>Deterministic metrics from the current or most recent live call. Empty values remain unscored rather than guessed.</p>
        </div>
        <span className="live-chat-profile-source">{report.eventCount} events</span>
      </header>

      <div className="live-chat-evaluation-grid">
        <Metric label="First audio" value={milliseconds(report.firstAudioLatencyMs.average)} detail={`p95 ${milliseconds(report.firstAudioLatencyMs.p95)}`} />
        <Metric label="Interruptions" value={percentage(report.interruptionSuccessRate)} detail={`cancel p95 ${milliseconds(report.cancellationLatencyMs.p95)}`} />
        <Metric label="False endpoints" value={percentage(report.falseEndpointRate)} detail={`${Math.round(report.talkOverDurationMs)} ms talk-over`} />
        <Metric label="Proactive acceptance" value={percentage(report.proactiveAcceptanceRate)} detail={`regret ${percentage(report.silenceFillRegretRate)}`} />
        <Metric label="Backchannel collisions" value={percentage(report.backchannelCollisionRate)} detail="Lower is better" />
        <Metric label="Question density" value={percentage(report.questionDensity)} detail="Assistant turns ending in questions" />
        <Metric label="Speaking balance" value={ratio(report.assistantUserSpeakingRatio)} detail="Assistant : user" />
        <Metric label="Turn duration" value={milliseconds(report.turnDurationMs.median)} detail={`p95 ${milliseconds(report.turnDurationMs.p95)}`} />
        <Metric label="Repair success" value={percentage(report.repairSuccessRate)} detail="Clarification and interruption repair" />
        <Metric label="Repeated topics" value={percentage(report.repeatedTopicRate)} detail="Lower is better" />
        <Metric label="Unanswered obligations" value={percentage(report.unansweredObligationRate)} detail="Lower is better" />
        <Metric label="Listening / pressure" value={`${score(report.perceivedListeningScore)} / ${score(report.perceivedPressureScore)}`} detail="User-scored, 1–5" />
      </div>

      <div className="live-chat-evaluation-survey">
        <label><span>Felt listened to</span><input aria-label="Perceived listening score" type="range" min="1" max="5" step="1" value={listeningScore} onChange={(event) => setListeningScore(Number(event.currentTarget.value))} /><strong>{listeningScore}/5</strong></label>
        <label><span>Felt pressured</span><input aria-label="Perceived pressure score" type="range" min="1" max="5" step="1" value={pressureScore} onChange={(event) => setPressureScore(Number(event.currentTarget.value))} /><strong>{pressureScore}/5</strong></label>
        <div><button type="button" onClick={saveSurvey}>Save experience score</button><button type="button" className="live-chat-secondary-action" onClick={reset}>Reset evaluation</button></div>
      </div>
      {status ? <p className="live-chat-note" role="status">{status}</p> : null}
    </section>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function percentage(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

function milliseconds(value: number | null): string {
  return value === null ? '—' : `${Math.round(value)} ms`;
}

function ratio(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(2)}×`;
}

function score(value: number | null): string {
  return value === null ? '—' : String(value);
}
