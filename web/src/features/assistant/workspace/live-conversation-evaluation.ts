export type EvaluationRole = 'user' | 'assistant';

export type LiveConversationEvaluationEvent =
  | { atMs: number; type: 'first_audio'; latencyMs: number }
  | { atMs: number; type: 'endpoint'; falsePositive: boolean }
  | { atMs: number; type: 'talk_over'; durationMs: number }
  | { atMs: number; type: 'interruption'; success: boolean; latencyMs?: number }
  | { atMs: number; type: 'proactive_prompt'; accepted: boolean | null }
  | { atMs: number; type: 'backchannel'; collision: boolean }
  | { atMs: number; type: 'turn'; role: EvaluationRole; durationMs: number; questionCount?: number; content?: string }
  | { atMs: number; type: 'repair'; success: boolean }
  | { atMs: number; type: 'topic'; repeated: boolean }
  | { atMs: number; type: 'obligation'; answered: boolean }
  | { atMs: number; type: 'survey'; listeningScore: number; pressureScore: number };

export type LiveConversationEvaluationReport = {
  eventCount: number;
  firstAudioLatencyMs: { average: number | null; p95: number | null };
  falseEndpointRate: number | null;
  talkOverDurationMs: number;
  interruptionSuccessRate: number | null;
  cancellationLatencyMs: { average: number | null; p95: number | null };
  silenceFillRegretRate: number | null;
  proactiveAcceptanceRate: number | null;
  backchannelCollisionRate: number | null;
  questionDensity: number | null;
  assistantUserSpeakingRatio: number | null;
  turnDurationMs: { median: number | null; p95: number | null };
  repairSuccessRate: number | null;
  repeatedTopicRate: number | null;
  unansweredObligationRate: number | null;
  perceivedListeningScore: number | null;
  perceivedPressureScore: number | null;
};

export function evaluateLiveConversation(events: LiveConversationEvaluationEvent[]): LiveConversationEvaluationReport {
  const ordered = [...events].sort((left, right) => left.atMs - right.atMs);
  const firstAudio = ordered.filter(isType('first_audio')).map((event) => nonNegative(event.latencyMs));
  const endpoints = ordered.filter(isType('endpoint'));
  const talkOver = ordered.filter(isType('talk_over')).reduce((sum, event) => sum + nonNegative(event.durationMs), 0);
  const interruptions = ordered.filter(isType('interruption'));
  const cancellationLatencies = interruptions.flatMap((event) => typeof event.latencyMs === 'number' ? [nonNegative(event.latencyMs)] : []);
  const proactive = ordered.filter(isType('proactive_prompt'));
  const resolvedProactive = proactive.filter((event) => event.accepted !== null);
  const backchannels = ordered.filter(isType('backchannel'));
  const turns = ordered.filter(isType('turn'));
  const assistantTurns = turns.filter((event) => event.role === 'assistant');
  const userDuration = turns.filter((event) => event.role === 'user').reduce((sum, event) => sum + nonNegative(event.durationMs), 0);
  const assistantDuration = assistantTurns.reduce((sum, event) => sum + nonNegative(event.durationMs), 0);
  const repairs = ordered.filter(isType('repair'));
  const topics = ordered.filter(isType('topic'));
  const obligations = ordered.filter(isType('obligation'));
  const surveys = ordered.filter(isType('survey'));
  const latestSurvey = surveys.at(-1);
  const turnDurations = turns.map((event) => nonNegative(event.durationMs));

  return {
    eventCount: ordered.length,
    firstAudioLatencyMs: averageDistribution(firstAudio),
    falseEndpointRate: rate(endpoints.filter((event) => event.falsePositive).length, endpoints.length),
    talkOverDurationMs: Math.round(talkOver),
    interruptionSuccessRate: rate(interruptions.filter((event) => event.success).length, interruptions.length),
    cancellationLatencyMs: averageDistribution(cancellationLatencies),
    silenceFillRegretRate: rate(resolvedProactive.filter((event) => event.accepted === false).length, resolvedProactive.length),
    proactiveAcceptanceRate: rate(resolvedProactive.filter((event) => event.accepted === true).length, resolvedProactive.length),
    backchannelCollisionRate: rate(backchannels.filter((event) => event.collision).length, backchannels.length),
    questionDensity: rate(
      assistantTurns.filter((event) => (event.questionCount ?? legacyQuestionCount(event.content)) > 0).length,
      assistantTurns.length,
    ),
    assistantUserSpeakingRatio: userDuration > 0 ? round(assistantDuration / userDuration) : null,
    turnDurationMs: turnDistribution(turnDurations),
    repairSuccessRate: rate(repairs.filter((event) => event.success).length, repairs.length),
    repeatedTopicRate: rate(topics.filter((event) => event.repeated).length, topics.length),
    unansweredObligationRate: rate(obligations.filter((event) => !event.answered).length, obligations.length),
    perceivedListeningScore: latestSurvey ? clampScore(latestSurvey.listeningScore) : null,
    perceivedPressureScore: latestSurvey ? clampScore(latestSurvey.pressureScore) : null,
  };
}

function isType<T extends LiveConversationEvaluationEvent['type']>(type: T) {
  return (event: LiveConversationEvaluationEvent): event is Extract<LiveConversationEvaluationEvent, { type: T }> => event.type === type;
}

function averageDistribution(values: number[]): { average: number | null; p95: number | null } {
  if (!values.length) return { average: null, p95: null };
  const sorted = [...values].sort((a, b) => a - b);
  return {
    average: Math.round(sorted.reduce((sum, value) => sum + value, 0) / sorted.length),
    p95: Math.round(percentile(sorted, 0.95)),
  };
}

function turnDistribution(values: number[]): { median: number | null; p95: number | null } {
  if (!values.length) return { median: null, p95: null };
  const sorted = [...values].sort((a, b) => a - b);
  return {
    median: Math.round(percentile(sorted, 0.5)),
    p95: Math.round(percentile(sorted, 0.95)),
  };
}

function percentile(sorted: number[], percentileValue: number): number {
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * percentileValue) - 1));
  return sorted[index] ?? 0;
}

function rate(numerator: number, denominator: number): number | null {
  return denominator > 0 ? round(numerator / denominator) : null;
}

function nonNegative(value: number): number {
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

function round(value: number): number {
  return Number(value.toFixed(3));
}

function clampScore(value: number): number {
  return Math.min(5, Math.max(1, Math.round(value)));
}

function legacyQuestionCount(content: string | undefined): number {
  return content && /[?？]\s*$/.test(content.trim()) ? 1 : 0;
}
