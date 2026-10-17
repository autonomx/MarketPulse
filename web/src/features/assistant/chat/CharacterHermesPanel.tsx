import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { characterHermesClient, type CharacterHermesSyncStatus } from './characterHermesClient';

function describe(status: CharacterHermesSyncStatus, action: 'import' | 'export'): string {
  if (!status.enabled) return 'Character Hermes sync is disabled by deployment policy.';
  if (!status.available) return status.skipped_reasons.join(', ') || 'Character Hermes storage is unavailable.';
  if (action === 'import') return `${status.imported_candidate_ids.length} pending suggestion(s) imported for review.`;
  return `${status.exported_memory_ids.length} approved character memory record(s) exported.`;
}

export function CharacterHermesPanel({ characterId }: { characterId: string }) {
  const [status, setStatus] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (action: 'import' | 'export') => action === 'import'
      ? characterHermesClient.import(characterId)
      : characterHermesClient.export(characterId),
    onSuccess: (result, action) => setStatus(describe(result, action)),
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Character Hermes operation failed.'),
  });

  return <div className="character-hermes-panel">
    <div><strong>Optional Hermes compatibility</strong><p>Import suggestions for review or export approved, normal-sensitivity character memories.</p></div>
    <div className="character-hermes-actions">
      <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate('import')}>Import for review</button>
      <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate('export')}>Export approved character memory</button>
    </div>
    {status ? <p role="status">{status}</p> : null}
  </div>;
}
