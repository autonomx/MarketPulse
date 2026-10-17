import { useEffect, useId, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  rpgWorldProfileClient,
  type RpgWorldGenreProfile,
  type RpgWorldProfileDomain,
  type RpgWorldProfileReview,
} from './api/rpgWorldProfileClient';
import './RpgWorldProfilePreview.css';

interface RpgWorldProfilePreviewProps {
  onApprovalChange?: (approved: boolean) => void;
  worldId: string;
}

const IMAGE_ROLES = [
  'none',
  'portrait',
  'scene',
  'landscape',
  'emblem',
  'icon',
  'illustration',
  'cover',
  'map',
];

function statusLabel(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function profileFromReview(review?: RpgWorldProfileReview): RpgWorldGenreProfile | undefined {
  const candidate = review?.profile;
  if (!candidate || !('domains' in candidate) || !Array.isArray(candidate.domains)) return undefined;
  return candidate as RpgWorldGenreProfile;
}

function cloneProfile(profile: RpgWorldGenreProfile): RpgWorldGenreProfile {
  return JSON.parse(JSON.stringify(profile)) as RpgWorldGenreProfile;
}

function range(domain: RpgWorldProfileDomain, key: 'quick' | 'standard' | 'epic'): [number, number] {
  const values = domain.target_range?.[key];
  return [Number(values?.[0] ?? 1), Number(values?.[1] ?? values?.[0] ?? 1)];
}

export function RpgWorldProfilePreview({ onApprovalChange, worldId }: RpgWorldProfilePreviewProps) {
  const queryClient = useQueryClient();
  const detailsId = useId();
  const profileQuery = useQuery({
    queryKey: ['feature', 'rpg', 'world-profile-review', worldId],
    queryFn: () => rpgWorldProfileClient.read(worldId),
    refetchInterval: (query) => query.state.data?.review.status === 'generating' ? 2500 : false,
  });
  const review = profileQuery.data?.review;
  const sourceProfile = useMemo(() => profileFromReview(review), [review]);
  const [draft, setDraft] = useState<RpgWorldGenreProfile>();
  const [dirty, setDirty] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (!sourceProfile) return;
    setDraft(cloneProfile(sourceProfile));
    setDirty(false);
  }, [review?.profile_hash, review?.profile_revision, sourceProfile]);

  useEffect(() => {
    onApprovalChange?.(review?.status === 'approved');
  }, [onApprovalChange, review?.status]);

  useEffect(() => {
    if (review?.status === 'approved') setCollapsed(true);
  }, [review?.status]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-profile-review', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-manifest', worldId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'world-authoring-section', worldId] }),
    ]);
  };

  const save = useMutation({
    mutationFn: () => {
      if (!review || !draft) throw new Error('No profile draft is available.');
      return rpgWorldProfileClient.update(worldId, review.profile_revision, draft);
    },
    onSuccess: async (result) => {
      setFeedback(`Profile revision ${result.review.profile_revision} saved and requires approval.`);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Profile could not be saved.'),
  });

  const approve = useMutation({
    mutationFn: () => {
      if (!review) throw new Error('No profile is available to approve.');
      return rpgWorldProfileClient.approve(worldId, review.profile_revision);
    },
    onSuccess: async (result) => {
      setFeedback(`Profile revision ${result.review.profile_revision} approved. World content generation is unlocked.`);
      setCollapsed(true);
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Profile could not be approved.'),
  });

  const retry = useMutation({
    mutationFn: () => rpgWorldProfileClient.retry(worldId),
    onSuccess: async () => {
      setFeedback('Profile generation restarted. This preview will refresh when the provider returns a result.');
      await refresh();
    },
    onError: (cause) => setFeedback(cause instanceof Error ? cause.message : 'Profile generation could not be retried.'),
  });

  const updateDomain = (index: number, changes: Partial<RpgWorldProfileDomain>) => {
    setDraft((current) => {
      if (!current) return current;
      const domains = current.domains.map((domain, domainIndex) => (
        domainIndex === index ? { ...domain, ...changes } : domain
      ));
      return { ...current, domains };
    });
    setDirty(true);
  };

  const updatePresentation = (index: number, changes: Record<string, unknown>) => {
    const domain = draft?.domains[index];
    if (!domain) return;
    const guidance = { ...(domain.generation_guidance ?? {}) };
    const presentation = {
      ...(guidance.presentation ?? {}),
      ...changes,
    };
    updateDomain(index, {
      generation_guidance: {
        ...guidance,
        presentation,
      },
    });
  };

  const updateStandardRange = (index: number, boundary: 0 | 1, value: number) => {
    const domain = draft?.domains[index];
    if (!domain) return;
    const currentRange = range(domain, 'standard');
    currentRange[boundary] = Math.max(1, Math.round(value || 1));
    if (currentRange[0] > currentRange[1]) {
      currentRange[boundary === 0 ? 1 : 0] = currentRange[boundary];
    }
    updateDomain(index, {
      target_range: {
        ...(domain.target_range ?? {}),
        standard: currentRange,
      },
    });
  };

  if (profileQuery.isPending) {
    return <section className="rpg-profile-preview"><h3>Profile Preview</h3><p>Loading the generated world profile…</p></section>;
  }
  if (profileQuery.isError) {
    return <section className="rpg-profile-preview is-error"><h3>Profile Preview</h3><p>{profileQuery.error instanceof Error ? profileQuery.error.message : 'Profile could not be loaded.'}</p></section>;
  }

  const status = review?.status ?? 'unresolved';
  const editable = Boolean(draft?.domains.length) && !['generating', 'unresolved'].includes(status);

  return (
    <section className={`rpg-profile-preview is-${status}`} aria-label="Genre profile preview">
      <header>
        <div>
          <p className="eyebrow">Approval gate</p>
          <div className="rpg-profile-preview-title-row">
            <h3>World Profile Preview</h3>
            <button
              aria-controls={detailsId}
              aria-expanded={!collapsed}
              className="rpg-profile-preview-toggle"
              onClick={() => setCollapsed((current) => !current)}
              type="button"
            >
              {collapsed ? 'Show details' : 'Hide details'}
            </button>
          </div>
          <p>Review the topic catalogue, presentation type, image role and generation size before any world lore is created.</p>
        </div>
        <span className="rpg-profile-preview-status">{statusLabel(status)}</span>
      </header>

      <div id={detailsId} hidden={collapsed}>
        {status === 'generating' ? <p>The profile architect is still generating the proposed catalogue.</p> : null}
        {status === 'validation_failed' ? <p className="rpg-authoring-feedback">Profile generation failed validation. Review the error details before retrying.</p> : null}
        {review?.error && Object.keys(review.error).length ? <pre>{JSON.stringify(review.error, null, 2)}</pre> : null}
        {status === 'validation_failed' ? (
          <button
            className="rpg-secondary-button"
            disabled={retry.isPending}
            onClick={() => retry.mutate()}
            type="button"
          >
            {retry.isPending ? 'Retrying Profile…' : 'Retry Profile Generation'}
          </button>
        ) : null}

        {draft?.domains.length ? (
          <>
            <div className="rpg-profile-preview-summary">
              <span><small>Profile</small><strong>{draft.display_name}</strong></span>
              <span><small>Revision</small><strong>{review?.profile_revision ?? 1}</strong></span>
              <span><small>Topics</small><strong>{draft.domains.length}</strong></span>
              <span><small>Source</small><strong>{review?.generated ? 'Generated' : 'Built-in flavour'}</strong></span>
            </div>

            <div className="rpg-profile-domain-table" role="table" aria-label="Proposed world topics">
            <div className="rpg-profile-domain-head" role="row">
              <span role="columnheader">Topic</span>
              <span role="columnheader">Entity kind</span>
              <span role="columnheader">Type</span>
              <span role="columnheader">Image</span>
              <span role="columnheader">Standard count</span>
              <span role="columnheader">Required</span>
            </div>
            {draft.domains.map((domain, index) => {
              const presentation = domain.generation_guidance?.presentation ?? {};
              const standard = range(domain, 'standard');
              return (
                <div className="rpg-profile-domain-row" role="row" key={domain.domain_id}>
                  <label role="cell">
                    <input
                      aria-label={`Title for ${domain.domain_id}`}
                      disabled={!editable}
                      value={domain.title}
                      onChange={(event) => updateDomain(index, { title: event.currentTarget.value })}
                    />
                    <small>{domain.domain_id}</small>
                    {domain.dependencies?.length ? <em>After: {domain.dependencies.join(', ')}</em> : <em>No dependencies</em>}
                  </label>
                  <label role="cell">
                    <input
                      aria-label={`Entity kind for ${domain.domain_id}`}
                      disabled={!editable}
                      value={domain.entity_kind}
                      onChange={(event) => updateDomain(index, { entity_kind: event.currentTarget.value })}
                    />
                  </label>
                  <label role="cell">
                    <select
                      aria-label={`Presentation type for ${domain.domain_id}`}
                      disabled={!editable}
                      value={String(presentation.page_kind ?? 'document')}
                      onChange={(event) => updatePresentation(index, { page_kind: event.currentTarget.value })}
                    >
                      <option value="collection">Card collection</option>
                      <option value="document">Document</option>
                    </select>
                    <small>{String(presentation.card_variant ?? domain.entity_kind)}</small>
                  </label>
                  <label role="cell">
                    <select
                      aria-label={`Image role for ${domain.domain_id}`}
                      disabled={!editable}
                      value={String(presentation.image_role ?? 'none')}
                      onChange={(event) => updatePresentation(index, { image_role: event.currentTarget.value })}
                    >
                      {IMAGE_ROLES.map((role) => <option key={role} value={role}>{statusLabel(role)}</option>)}
                    </select>
                  </label>
                  <div role="cell" className="rpg-profile-range-inputs">
                    <input
                      aria-label={`Minimum standard count for ${domain.domain_id}`}
                      disabled={!editable}
                      min={1}
                      type="number"
                      value={standard[0]}
                      onChange={(event) => updateStandardRange(index, 0, Number(event.currentTarget.value))}
                    />
                    <span>–</span>
                    <input
                      aria-label={`Maximum standard count for ${domain.domain_id}`}
                      disabled={!editable}
                      min={1}
                      type="number"
                      value={standard[1]}
                      onChange={(event) => updateStandardRange(index, 1, Number(event.currentTarget.value))}
                    />
                  </div>
                  <label role="cell" className="rpg-profile-required-toggle">
                    <input
                      aria-label={`Required before launch for ${domain.domain_id}`}
                      checked={Boolean(domain.required_before_launch)}
                      disabled={!editable}
                      type="checkbox"
                      onChange={(event) => updateDomain(index, { required_before_launch: event.currentTarget.checked })}
                    />
                    <span>{domain.required_before_launch ? 'Yes' : 'Optional'}</span>
                  </label>
                </div>
              );
            })}
            </div>

            <details className="rpg-profile-json-preview">
            <summary>Advanced profile JSON</summary>
            <textarea
              aria-label="Advanced profile JSON"
              disabled={!editable}
              rows={18}
              value={JSON.stringify(draft, null, 2)}
              onChange={(event) => {
                try {
                  setDraft(JSON.parse(event.currentTarget.value) as RpgWorldGenreProfile);
                  setDirty(true);
                  setFeedback('');
                } catch {
                  setFeedback('Advanced profile JSON is not valid yet.');
                }
              }}
            />
            </details>

            <div className="rpg-profile-preview-actions">
            <button type="button" disabled={!editable || !dirty || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? 'Saving…' : 'Save Profile Draft'}
            </button>
            <button
              className="rpg-secondary-button"
              type="button"
              disabled={!editable || dirty || approve.isPending || status === 'approved'}
              onClick={() => approve.mutate()}
            >
              {approve.isPending ? 'Approving…' : status === 'approved' ? 'Profile Approved' : 'Approve Profile'}
            </button>
            {dirty ? <small>Save this revision before approval.</small> : null}
            </div>
          </>
        ) : status !== 'generating' ? <p>No valid profile domains are available yet.</p> : null}

        {feedback ? <p className="rpg-authoring-feedback" aria-live="polite">{feedback}</p> : null}
      </div>
    </section>
  );
}
