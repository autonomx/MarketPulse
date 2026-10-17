import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { characterAvatarClient } from './characterAvatarClient';

const ACTIVE_STATES = new Set(['queued', 'generating_base', 'generating_variants']);

export function CharacterVoiceBackfillButton() {
  const queryClient = useQueryClient();
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [visemeBatchIds, setVisemeBatchIds] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const visemeStartedCharacters = useRef(new Set<string>());
  const progressQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-avatar-backfill-progress', batchIds],
    queryFn: () => Promise.all(batchIds.map((batchId) => characterAvatarClient.generation(batchId))),
    enabled: batchIds.length > 0,
    refetchInterval: (query) => (query.state.data ?? []).some((batch) => ACTIVE_STATES.has(batch.status)) ? 2_000 : false,
  });
  const visemeProgressQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-avatar-backfill-visemes', visemeBatchIds],
    queryFn: () => Promise.all(visemeBatchIds.map((batchId) => characterAvatarClient.visemeGeneration(batchId))),
    enabled: visemeBatchIds.length > 0,
    refetchInterval: (query) => (query.state.data ?? []).some((batch) => batch.status === 'generating') ? 2_000 : false,
  });
  const mutation = useMutation({
    mutationFn: () => characterAvatarClient.backfillClonedVoices({
      queue_avatar_generation: true,
      appearance_template: 'Create an original fictional conversational companion suitable for a polished live-chat portrait. Do not depict or imitate a real public person.',
      style: 'illustrated character portrait',
      include_reference_profiles: false,
    }),
    onSuccess: (response) => {
      const ids = response.items.map((item) => item.generation_batch_id).filter((value): value is string => Boolean(value));
      visemeStartedCharacters.current.clear();
      setBatchIds(ids);
      setVisemeBatchIds([]);
      const created = response.items.filter((item) => item.result === 'created').length;
      const skipped = response.items.filter((item) => item.result === 'skipped').length;
      const failed = response.items.filter((item) => item.result === 'failed').length;
      setStatus(`${created} profiles created · ${ids.length} avatar batches queued · ${skipped} skipped · ${failed} failed`);
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'characters'] });
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Cloned-voice backfill failed.'),
  });

  useEffect(() => {
    const batches = progressQuery.data ?? [];
    if (!batches.length) return;
    const completed = batches.filter((batch) => batch.status === 'completed').length;
    const failed = batches.filter((batch) => batch.status === 'failed').length;
    const active = batches.length - completed - failed;
    setStatus(`${completed} cloned-voice base avatars ready · ${active} generating · ${failed} failed`);
    for (const batch of batches) {
      if (batch.status !== 'completed' || visemeStartedCharacters.current.has(batch.character_id)) continue;
      visemeStartedCharacters.current.add(batch.character_id);
      void characterAvatarClient.createVisemeGeneration(batch.character_id).then((visemeBatch) => {
        setVisemeBatchIds((current) => current.includes(visemeBatch.id) ? current : [...current, visemeBatch.id]);
      }).catch((error: unknown) => {
        setStatus(error instanceof Error ? `${batch.character_id}: ${error.message}` : `${batch.character_id}: precise viseme generation failed.`);
      });
    }
    if (active === 0) {
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'characters'] });
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-avatar-pack'] });
    }
  }, [progressQuery.data, queryClient]);

  useEffect(() => {
    const batches = visemeProgressQuery.data ?? [];
    if (!batches.length) return;
    const completed = batches.filter((batch) => batch.status === 'completed').length;
    const failed = batches.filter((batch) => batch.status === 'failed').length;
    const active = batches.length - completed - failed;
    setStatus(`${completed} cloned-voice avatars have precise visemes · ${active} upgrading · ${failed} fell back to audio-envelope lip sync`);
    if (active === 0) {
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-avatar-pack'] });
    }
  }, [queryClient, visemeProgressQuery.data]);

  return <div className="character-voice-backfill">
    <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
      {mutation.isPending ? 'Discovering cloned voices…' : 'Create characters from cloned voices'}
    </button>
    {status ? <small role="status">{status}</small> : null}
  </div>;
}
