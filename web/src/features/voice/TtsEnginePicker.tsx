import { Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { voiceApiClient, type TtsEngineSwitchPhase, type TtsEnginesResponse } from './api/voiceClient';

export const ENGINES_QUERY_KEY = ['voice', 'tts-engines'] as const;
const BUSY_PHASES: ReadonlySet<TtsEngineSwitchPhase> = new Set(['unloading', 'preparing_voices', 'loading']);

/** Choose Omnix's TTS engine. One engine is loaded at a time; the choice becomes the default everywhere. */
export function TtsEnginePicker() {
  const queryClient = useQueryClient();
  const enginesQuery = useQuery({
    queryKey: ENGINES_QUERY_KEY,
    queryFn: () => voiceApiClient.listTtsEngines(),
    refetchInterval: (query) => (query.state.data && BUSY_PHASES.has(query.state.data.switch.phase) ? 1500 : false),
  });
  const selectMutation = useMutation({
    mutationFn: (providerId: string) => voiceApiClient.selectTtsEngine(providerId),
    onSuccess: (data) => queryClient.setQueryData(ENGINES_QUERY_KEY, data),
  });
  const data = enginesQuery.data;
  const phase = data?.switch.phase ?? 'idle';
  const busy = BUSY_PHASES.has(phase) || selectMutation.isPending;

  // When a switch settles, views that show the default engine refresh.
  const previousPhase = useRef(phase);
  useEffect(() => {
    if (BUSY_PHASES.has(previousPhase.current) && !BUSY_PHASES.has(phase)) {
      void queryClient.invalidateQueries({ queryKey: ['settings', 'profile'] });
      void queryClient.invalidateQueries({ queryKey: ['platform', 'providers'] });
    }
    previousPhase.current = phase;
  }, [phase, queryClient]);

  if (enginesQuery.isError) {
    return <div className="tts-engine-picker"><Text size="xs" role="status">TTS engines could not be listed.</Text></div>;
  }
  return (
    <div className="tts-engine-picker">
      <label>
        <span>TTS engine</span>
        <select
          aria-label="TTS engine"
          disabled={!data || busy}
          value={data?.selected ?? ''}
          onChange={(event) => selectMutation.mutate(event.currentTarget.value)}
        >
          {data?.engines.map((engine) => (
            <option key={engine.id} value={engine.id} disabled={!engine.installed && !engine.selected} title={engine.unavailable_reason || engine.description}>
              {engine.label}{engine.installed ? '' : ' (not installed)'}
            </option>
          ))}
        </select>
      </label>
      {data ? <EngineSwitchStatus data={data} /> : null}
      {selectMutation.isError ? <Text size="xs" role="alert">{errorMessage(selectMutation.error)}</Text> : null}
    </div>
  );
}

function EngineSwitchStatus({ data }: { data: TtsEnginesResponse }) {
  const { switch: state } = data;
  const label = data.engines.find((engine) => engine.id === (state.provider ?? data.selected))?.label ?? state.provider ?? data.selected;
  const voices = state.voices;
  const voiceNote = voices.prepared.length || voices.failed.length
    ? `${voices.prepared.length} voice${voices.prepared.length === 1 ? '' : 's'} transcribed for cloning`
      + (voices.failed.length ? `; ${voices.failed.length} could not be: ${voices.failed.map((item) => item.voice_id.replace(/^voice-cloning:/, '')).join(', ')}` : '')
    : '';
  if (state.phase === 'failed') {
    return <Text size="xs" role="alert">{label} did not load: {state.error || 'unknown error'}</Text>;
  }
  const message = {
    idle: '',
    unloading: 'Unloading the previous engine…',
    preparing_voices: 'Transcribing voice samples that have no transcript…',
    loading: `Loading ${label}…`,
    ready: `${label} is loaded.`,
  }[state.phase];
  if (!message && !voiceNote) return null;
  return <Text size="xs" role="status">{[message, voiceNote].filter(Boolean).join(' ')}</Text>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The TTS engine could not be changed.';
}
