import { unwrap } from '../../../api/http';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';
import { api } from '../api/gateway';


export type LiveTtsCapabilities = {
  ok: boolean;
  protocol: string;
  persistent_websocket: boolean;
  incremental_text_ingest: boolean;
  text_commit_deadline_ms: number;
  text_commit_minimum_characters: number;
  streaming_audio_chunks: boolean;
  native_decoder_text_append: boolean;
  stateful_text_append: boolean;
  prosody_continuous_decoder: boolean;
  cancellation_generations: boolean;
  adaptive_playback_buffer: boolean;
  fallback_mode: string;
  provider_available: boolean;
  provider_name?: string | null;
};

let installed = false;
let negotiatedCapabilities: LiveTtsCapabilities | null = null;

export function initializeLiveTtsCapabilityController(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (installed) return () => undefined;
  installed = true;
  const abortController = new AbortController();
  void unwrap(api.GET('/api/tts/live-call/capabilities', {
    cache: 'no-store',
    signal: abortController.signal,
  })).then((payload) => {
    // The capability route returns an untyped object.
    const capabilities = payload as unknown as LiveTtsCapabilities;
    negotiatedCapabilities = capabilities;
    emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, {
        stage: 'tts_capabilities_negotiated',
        timestamp: new Date().toISOString(),
        ...capabilities,
      });
  }).catch((error: unknown) => {
    if (abortController.signal.aborted) return;
    emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, {
        stage: 'tts_capabilities_unavailable',
        timestamp: new Date().toISOString(),
        error: error instanceof Error ? error.message : String(error),
      });
  });

  return () => {
    abortController.abort('controller-uninstalled');
    negotiatedCapabilities = null;
    installed = false;
  };
}

export function readLiveTtsCapabilities(): LiveTtsCapabilities | null {
  return negotiatedCapabilities;
}
