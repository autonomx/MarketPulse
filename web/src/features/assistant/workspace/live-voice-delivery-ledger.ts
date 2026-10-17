import type { LiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
import { liveVoiceTranscriptStore } from './live-voice-transcript-store';

const DEFAULT_PLAYBACK_SAMPLE_RATE = 24_000;

export type LiveVoiceDeliveryPhrase = {
  phraseIndex: number;
  text: string;
  textStart: number;
  textEnd: number;
  audioSampleStart: number | null;
  audioSampleEnd: number | null;
};

export type LiveVoiceDeliveryLedger = {
  assistantTurnId: string | null;
  generatedText: string;
  phrases: LiveVoiceDeliveryPhrase[];
  playbackSampleRate: number;
  semanticSpeechSamples: number;
  audioPlayedSamples: number;
  audioDeliveredPhraseCount: number;
  activePhraseIndex: number | null;
  visualDeliveredTextEnd: number;
  contextDeliveredTextEnd: number;
};

export function createLiveVoiceDeliveryLedger(
  playbackSampleRate = DEFAULT_PLAYBACK_SAMPLE_RATE,
): LiveVoiceDeliveryLedger {
  return {
    assistantTurnId: null,
    generatedText: '',
    phrases: [],
    playbackSampleRate: positiveInteger(playbackSampleRate, DEFAULT_PLAYBACK_SAMPLE_RATE),
    semanticSpeechSamples: 0,
    audioPlayedSamples: 0,
    audioDeliveredPhraseCount: 0,
    activePhraseIndex: null,
    visualDeliveredTextEnd: 0,
    contextDeliveredTextEnd: 0,
  };
}

export function appendDeliveryPhrase(
  ledger: LiveVoiceDeliveryLedger,
  phraseIndex: number,
  text: string,
): void {
  const normalized = text.trim();
  const separator = ledger.generatedText ? ' ' : '';
  const textStart = ledger.generatedText.length + separator.length;
  ledger.generatedText = `${ledger.generatedText}${separator}${normalized}`;
  ledger.phrases.push({
    phraseIndex,
    text: normalized,
    textStart,
    textEnd: ledger.generatedText.length,
    audioSampleStart: null,
    audioSampleEnd: null,
  });
}

export function handleDeliveryDiagnostic(
  ledger: LiveVoiceDeliveryLedger,
  event: string,
  details: Record<string, unknown>,
): boolean {
  const reportedSampleRate = finiteNumber(details.sample_rate, 0);
  if (reportedSampleRate > 0) ledger.playbackSampleRate = Math.round(reportedSampleRate);

  if (event === 'phrase_buffered') {
    const phraseIndex = finiteNumber(details.phrase_index, -1);
    const phrase = ledger.phrases.find((item) => item.phraseIndex === phraseIndex);
    if (!phrase) return false;
    const precedingPhrase = ledger.phrases
      .filter((item) => item.phraseIndex < phraseIndex)
      .sort((left, right) => right.phraseIndex - left.phraseIndex)[0];
    phrase.audioSampleStart = precedingPhrase?.audioSampleEnd ?? 0;
    const playbackSamples = finiteNumber(details.playback_samples, -1);
    const sampleLength = playbackSamples >= 0
      ? Math.max(1, Math.round(playbackSamples))
      : Math.max(
        1,
        Math.round(
          finiteNumber(details.audio_ms, 0) * ledger.playbackSampleRate / 1000,
        ),
      );
    phrase.audioSampleEnd = phrase.audioSampleStart + sampleLength;
    return advanceDeliveryLedger(ledger, ledger.semanticSpeechSamples);
  }
  if (!event.startsWith('worklet_')) return false;
  const semanticSamples = finiteNumber(
    details.semantic_speech_samples,
    finiteNumber(details.played_samples, ledger.semanticSpeechSamples),
  );
  return advanceDeliveryLedger(ledger, semanticSamples);
}

export function advanceDeliveryLedger(
  ledger: LiveVoiceDeliveryLedger,
  semanticSpeechSamples: number,
): boolean {
  const finalSample = ledger.phrases.at(-1)?.audioSampleEnd ?? ledger.semanticSpeechSamples;
  const boundedSamples = semanticSpeechSamples === Number.MAX_SAFE_INTEGER
    ? finalSample
    : semanticSpeechSamples;
  ledger.semanticSpeechSamples = Number.isFinite(boundedSamples)
    ? Math.max(ledger.semanticSpeechSamples, boundedSamples)
    : ledger.semanticSpeechSamples;
  ledger.audioPlayedSamples = ledger.semanticSpeechSamples;
  let changed = false;
  for (const phrase of ledger.phrases) {
    if (phrase.audioSampleStart === null || phrase.audioSampleEnd === null) continue;
    if (ledger.semanticSpeechSamples > phrase.audioSampleStart && ledger.visualDeliveredTextEnd < phrase.textEnd) {
      ledger.visualDeliveredTextEnd = phrase.textEnd;
      ledger.activePhraseIndex = phrase.phraseIndex;
      changed = true;
    }
    if (ledger.semanticSpeechSamples >= phrase.audioSampleEnd && ledger.contextDeliveredTextEnd < phrase.textEnd) {
      ledger.contextDeliveredTextEnd = phrase.textEnd;
      ledger.audioDeliveredPhraseCount = Math.max(ledger.audioDeliveredPhraseCount, phrase.phraseIndex + 1);
      ledger.activePhraseIndex = null;
      changed = true;
    }
  }
  return changed;
}

export function renderDeliveryLedger(
  ledger: LiveVoiceDeliveryLedger,
  partial = false,
): void {
  if (ledger.visualDeliveredTextEnd <= 0) return;
  liveVoiceTranscriptStore.setDelivery({ text: ledger.generatedText.slice(0, ledger.visualDeliveredTextEnd).trim(), partial });
}

export function removeDeliveryLedgerRow(): void {
  liveVoiceTranscriptStore.setDelivery(null);
}

export function instrumentDeliveryReporter(
  reporter: LiveCallDiagnosticsReporter,
  getLedger: () => LiveVoiceDeliveryLedger | null,
  onChanged: (ledger: LiveVoiceDeliveryLedger) => void,
): LiveCallDiagnosticsReporter {
  const originalRecord = reporter.record.bind(reporter);
  reporter.record = (event, details = {}, source = 'browser') => {
    originalRecord(event, details, source);
    const ledger = getLedger();
    if (ledger && handleDeliveryDiagnostic(ledger, event, details)) onChanged(ledger);
  };
  return reporter;
}

function finiteNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function positiveInteger(value: number, fallback: number): number {
  return Number.isFinite(value) && value > 0 ? Math.round(value) : fallback;
}
