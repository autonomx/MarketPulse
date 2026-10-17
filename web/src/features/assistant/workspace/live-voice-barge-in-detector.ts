export type AcousticBargeInDecision = 'independent_speech' | 'likely_echo' | 'uncertain' | 'no_playback';

export type AcousticBargeInInput = {
  assistantSpeaking: boolean;
  microphoneRms: number;
  playbackRms: number;
  playbackReferenceAgeMs: number;
  speechThreshold: number;
  waveformSimilarity?: number | null;
  residualSpeechRatio?: number | null;
  estimatedEchoGain?: number | null;
  calibratedEchoGain?: number | null;
  interruptionSensitivity?: number;
};

export type AcousticBargeInAssessment = {
  decision: AcousticBargeInDecision;
  confidence: number;
  reason: string;
  microphoneRms: number;
  playbackRms: number;
  energyRatio: number | null;
  waveformSimilarity: number | null;
  residualSpeechRatio: number | null;
  estimatedEchoGain: number | null;
};

// TTS frames can be buffered ahead of physical speaker playback. Keep the
// reference valid long enough to cover the worklet buffer plus room/mic delay.
const MAX_REFERENCE_AGE_MS = 3_000;
const MIN_REFERENCE_RMS = 0.002;

export function calculatePcm16Rms(samples: Int16Array): number {
  if (!samples.length) return 0;
  let sum = 0;
  for (const sample of samples) {
    const normalized = sample / 32768;
    sum += normalized * normalized;
  }
  return Math.sqrt(sum / samples.length);
}

export function assessAcousticBargeIn(input: AcousticBargeInInput): AcousticBargeInAssessment {
  const microphoneRms = finiteNonNegative(input.microphoneRms);
  const playbackRms = finiteNonNegative(input.playbackRms);
  const referenceAge = finiteNonNegative(input.playbackReferenceAgeMs);
  const threshold = Math.max(0.001, finiteNonNegative(input.speechThreshold));
  const energyRatio = playbackRms > 0 ? microphoneRms / playbackRms : null;
  const waveformSimilarity = finiteOptionalRatio(input.waveformSimilarity);
  const residualSpeechRatio = finiteOptionalRatio(input.residualSpeechRatio);
  const estimatedEchoGain = finiteOptionalSigned(input.estimatedEchoGain);
  const echoGain = finiteOptionalRatio(input.calibratedEchoGain);
  const sensitivity = clamp01(input.interruptionSensitivity ?? 0.7);

  if (!input.assistantSpeaking) {
    return assessment(
      'no_playback', 1, 'assistant_not_speaking', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }
  if (microphoneRms < threshold) {
    return assessment(
      'likely_echo', 0.85, 'below_user_speech_threshold', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }
  if (referenceAge > MAX_REFERENCE_AGE_MS || playbackRms < MIN_REFERENCE_RMS) {
    return assessment(
      'independent_speech', 0.74, 'no_recent_playback_reference', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }

  const strongEchoSimilarity = 0.54 + sensitivity * 0.1;
  const weakEchoSimilarity = 0.42 + sensitivity * 0.08;
  const nearCertainEchoSimilarity = 0.9;
  const maximumEchoResidual = 0.55 - sensitivity * 0.1;
  const independentResidual = 0.76 - sensitivity * 0.16;
  const calibratedEchoRatio = echoGain === null ? 0.58 : Math.max(0.35, Math.min(0.78, echoGain * 2.2));
  const independentRatio = 1.08 - sensitivity * 0.25;

  // A least-squares subtraction of the delayed assistant waveform leaves very
  // little residual for speaker echo. Real user speech remains in the residual,
  // including when the user and assistant overlap.
  if (
    waveformSimilarity !== null
    && residualSpeechRatio !== null
    && waveformSimilarity >= strongEchoSimilarity
    && residualSpeechRatio <= maximumEchoResidual
  ) {
    return assessment(
      'likely_echo', 0.97, 'echo_residual_matches_playback', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }
  if (
    residualSpeechRatio !== null
    && residualSpeechRatio >= independentResidual
    && microphoneRms >= threshold
  ) {
    return assessment(
      'independent_speech', 0.93, 'residual_speech_exceeds_echo', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }

  // Very high correlation is safe to treat as self playback even when speaker
  // or microphone gain makes a simple energy-ratio test misleading.
  if (waveformSimilarity !== null && waveformSimilarity >= nearCertainEchoSimilarity) {
    return assessment(
      'likely_echo', 0.96, 'waveform_matches_playback', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }
  if (waveformSimilarity !== null && waveformSimilarity >= strongEchoSimilarity) {
    if (energyRatio === null || energyRatio <= Math.max(0.72, calibratedEchoRatio)) {
      return assessment(
        'likely_echo', 0.94, 'waveform_matches_playback', microphoneRms, playbackRms,
        energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
      );
    }
  }
  if (waveformSimilarity !== null && waveformSimilarity <= 0.22 && energyRatio !== null && energyRatio >= 0.58) {
    return assessment(
      'independent_speech', 0.9, 'waveform_separates_from_playback', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }
  if (energyRatio !== null && energyRatio <= 0.48 && (waveformSimilarity === null || waveformSimilarity >= weakEchoSimilarity)) {
    return assessment(
      'likely_echo', 0.82, 'microphone_energy_tracks_playback', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }
  if (energyRatio !== null && energyRatio >= independentRatio) {
    return assessment(
      'independent_speech', 0.8, 'microphone_energy_exceeds_echo_envelope', microphoneRms, playbackRms,
      energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
    );
  }
  return assessment(
    'uncertain', 0.5, 'candidate_requires_partial_stt', microphoneRms, playbackRms,
    energyRatio, waveformSimilarity, residualSpeechRatio, estimatedEchoGain,
  );
}

function assessment(
  decision: AcousticBargeInDecision,
  confidence: number,
  reason: string,
  microphoneRms: number,
  playbackRms: number,
  energyRatio: number | null,
  waveformSimilarity: number | null,
  residualSpeechRatio: number | null,
  estimatedEchoGain: number | null,
): AcousticBargeInAssessment {
  return {
    decision,
    confidence,
    reason,
    microphoneRms,
    playbackRms,
    energyRatio,
    waveformSimilarity,
    residualSpeechRatio,
    estimatedEchoGain,
  };
}

function finiteNonNegative(value: number): number {
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}

function finiteOptionalRatio(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? clamp01(value) : null;
}

function finiteOptionalSigned(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}
