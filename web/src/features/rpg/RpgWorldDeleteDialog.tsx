import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  rpgWorldDeletionClient,
  type RpgWorldDeletionResponse,
} from './api/rpgWorldDeletionClient';
import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import './RpgWorldDeleteDialog.css';

interface RpgWorldDeleteDialogProps {
  onCancel: () => void;
  onDeleted: (result: RpgWorldDeletionResponse) => void;
  world: RpgWorldSummary;
}

function humanize(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function RpgWorldDeleteDialog({ onCancel, onDeleted, world }: RpgWorldDeleteDialogProps) {
  const [confirmation, setConfirmation] = useState('');
  const [feedback, setFeedback] = useState('');
  const eligibilityQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-deletion-eligibility', world.id],
    queryFn: () => rpgWorldDeletionClient.eligibility(world.id),
    retry: false,
  });
  const eligibility = eligibilityQuery.data?.eligibility;
  const deletionPreview = useMemo(
    () => Object.entries(eligibility?.deleted_counts ?? {}).filter(([, count]) => count > 0),
    [eligibility?.deleted_counts],
  );
  const deletion = useMutation({
    mutationFn: () => rpgWorldDeletionClient.delete(world.id, confirmation),
    onSuccess: onDeleted,
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'World could not be deleted.'),
  });

  useEffect(() => {
    setConfirmation('');
    setFeedback('');
  }, [world.id]);

  const confirmed = confirmation === world.title;
  const canSubmit = Boolean(eligibility?.can_delete && confirmed && !deletion.isPending);

  return (
    <div className="rpg-authoring-modal rpg-world-delete-modal" role="dialog" aria-modal="true" aria-label={`Delete ${world.title}`}>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) deletion.mutate();
      }}>
        <div className="rpg-world-delete-heading">
          <div><p className="eyebrow">Permanent action</p><h3>Delete {world.title}?</h3></div>
          <span aria-hidden="true">!</span>
        </div>
        <p>
          Permanently delete this world at any state. This also removes its authoring and published
          content, including scenarios, maps, campaigns bound to this world, and active generation work.
        </p>

        {eligibilityQuery.isPending ? <p>Checking deletion safety…</p> : null}
        {eligibilityQuery.isError ? (
          <p className="rpg-world-catalog-error">Unable to verify whether this world can be deleted.</p>
        ) : null}

        {eligibility?.can_delete ? (
          <>
            <section className="rpg-world-delete-preview" aria-label="Content to be deleted">
              <h4>This permanently removes the world and its associated data</h4>
              {deletionPreview.length ? (
                <dl>
                  {deletionPreview.map(([label, count]) => (
                    <div key={label}><dt>{humanize(label)}</dt><dd>{count}</dd></div>
                  ))}
                </dl>
              ) : <p>The empty world project will be removed.</p>}
              <small>Shared asset files are retained; their world bindings are removed.</small>
            </section>
            <label>
              <span>Type <strong>{world.title}</strong> to confirm</span>
              <input
                autoComplete="off"
                autoFocus
                aria-label={`Type ${world.title} to confirm deletion`}
                value={confirmation}
                onChange={(event) => setConfirmation(event.currentTarget.value)}
              />
            </label>
          </>
        ) : null}

        {feedback ? <p className="rpg-world-catalog-error" aria-live="polite">{feedback}</p> : null}
        <div>
          <button className="rpg-secondary-button" type="button" onClick={onCancel}>Cancel</button>
          <button className="rpg-danger-button" type="submit" disabled={!canSubmit}>
            {deletion.isPending ? 'Deleting…' : 'Delete Permanently'}
          </button>
        </div>
      </form>
    </div>
  );
}
