/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { api } from './api/gateway';

import type { components } from './api/generated';
type LoreDocumentSummary = components['schemas']['RpgLoreDocumentSummary'];

interface LoreDocumentDetail extends LoreDocumentSummary {
  full_text: string;
}

type LoreDossier = components['schemas']['RpgLoreDossier'];

type LoreResponse = components['schemas']['RpgCampaignLoreResponse'];

interface RpgLorePanelProps {
  labelledById?: string;
  panelId?: string;
  role?: 'region' | 'tabpanel';
}

interface LoreEntry {
  id: string;
  title: string;
  category: string;
  kind: 'document' | 'dossier';
  status: string;
  summary: string;
  document?: LoreDocumentSummary;
  dossier?: LoreDossier;
}

const LORE_CATEGORY_ORDER = [
  'World Lore',
  'Regions',
  'Areas',
  'Points of Interest',
  'Locations',
  'Characters',
  'Races',
  'Classes',
  'Factions',
  'Institutions',
  'Monsters',
  'Items',
  'Spells',
  'Feats',
  'Quests',
  'History & Calendar',
  'Conflicts',
  'Discoveries',
] as const;

function selectedSessionId(): string {
  if (typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem('omnix:rpg:selected-session-id')?.trim() ?? '';
  } catch {
    return '';
  }
}

function statusLabel(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function dossierSummary(dossier: LoreDossier): string {
  const text = dossier.description
    || dossier.sensory_profile
    || dossier.appearance
    || dossier.personality
    || dossier.public_goal
    || dossier.goals?.[0]
    || dossier.values?.join(', ')
    || 'Known to the player, with more details discoverable through play.';
  return String(text);
}

function dossierFacts(dossier: LoreDossier): Array<{ label: string; value: string }> {
  const fields: Array<[string, unknown]> = [
    ['Appearance', dossier.appearance],
    ['Personality', dossier.personality],
    ['Speech style', dossier.speech_style],
    ['Role', dossier.role],
    ['Location', dossier.location_id],
    ['Factions', dossier.faction_ids],
    ['Region', dossier.region_id],
    ['Atmosphere', dossier.sensory_profile],
    ['Services', dossier.services],
    ['Values', dossier.values],
    ['Public goal', dossier.public_goal],
    ['Goals', dossier.goals],
  ];
  return fields.flatMap(([label, raw]) => {
    const value = Array.isArray(raw) ? raw.join(', ') : String(raw ?? '').trim();
    return value ? [{ label, value }] : [];
  });
}

function normalizedCategory(value: string): string {
  const label = value.trim();
  return LORE_CATEGORY_ORDER.includes(label as typeof LORE_CATEGORY_ORDER[number]) ? label : 'World Lore';
}

type GatewayCall = Promise<{ data?: unknown; error?: unknown; response: Response }>;

// The lore routes return untyped objects; T is the shape this panel reads.
async function readLore<T>(call: GatewayCall): Promise<T> {
  const { data, response } = await call;
  if (!response.ok) throw new Error(`Lore request failed (${response.status})`);
  return data as T;
}

type RuntimeLoreKind = 'creature' | 'location';

async function generateLore<T>(call: GatewayCall): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) {
    const detail = (error as { detail?: { message?: string } } | undefined)?.detail;
    throw new Error(detail?.message || `Lore generation failed (${response.status})`);
  }
  return data as T;
}

export function RpgLorePanel({
  labelledById = 'rpg-lore-tab',
  panelId = 'rpg-lore-panel',
  role = 'tabpanel',
}: RpgLorePanelProps = {}) {
  const sessionId = selectedSessionId();
  const [lore, setLore] = useState<LoreResponse | null>(null);
  const [selectedId, setSelectedId] = useState('overview');
  const [detail, setDetail] = useState<LoreDocumentDetail | null>(null);
  const [error, setError] = useState('');
  const [direction, setDirection] = useState('');
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [newLoreKind, setNewLoreKind] = useState<RuntimeLoreKind>('creature');
  const [newLoreName, setNewLoreName] = useState('');
  const [newLoreDirection, setNewLoreDirection] = useState('');
  const [isMaterializing, setIsMaterializing] = useState(false);

  useEffect(() => {
    let active = true;
    setLore(null);
    setDetail(null);
    setSelectedId('overview');
    setError('');
    if (!sessionId) return () => { active = false; };
    readLore<LoreResponse>(api.GET('/api/rpg/sessions/{session_id}/lore', { params: { path: { session_id: sessionId } }, cache: 'no-store' }))
      .then((payload) => {
        if (!active) return;
        setLore(payload);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Lore could not be loaded.');
      });
    return () => { active = false; };
  }, [sessionId]);

  const entries = useMemo<LoreEntry[]>(() => {
    if (!lore) return [];
    const documents = lore.categories.flatMap((category) => category.documents.map((document) => ({
      id: document.document_id,
      title: document.title,
      category: normalizedCategory(document.category || category.label),
      kind: 'document' as const,
      status: document.status,
      summary: document.summary_120 || document.summary_500,
      document,
    })));
    const dossierGroups: Array<{ category: string; rows: LoreDossier[] }> = [
      { category: 'Characters', rows: lore.dossiers?.characters ?? [] },
      { category: 'Locations', rows: lore.dossiers?.locations ?? [] },
      { category: 'Factions', rows: lore.dossiers?.factions ?? [] },
    ];
    const dossiers = dossierGroups.flatMap((group) => group.rows.map((dossier) => ({
      id: `dossier:${dossier.kind}:${dossier.id}`,
      title: dossier.name,
      category: group.category,
      kind: 'dossier' as const,
      status: dossier.status,
      summary: dossierSummary(dossier),
      dossier,
    })));
    const seen = new Set<string>();
    return [...documents, ...dossiers].filter((entry) => {
      const key = `${entry.category}:${entry.title.toLocaleLowerCase()}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [lore]);

  const selectedEntry = entries.find((entry) => entry.id === selectedId) ?? null;
  const selectedDocumentId = selectedEntry?.kind === 'document' ? selectedEntry.id : '';
  const selectedDetail = detail?.document_id === selectedDocumentId ? detail : null;
  const selectedRuntimeKind: RuntimeLoreKind | null = selectedEntry?.category === 'Monsters'
    ? 'creature'
    : selectedEntry?.category === 'Locations'
      ? 'location'
      : null;
  const selectEntry = (entryId: string) => {
    setDetail(null);
    setError('');
    setSelectedId(entryId);
  };

  useEffect(() => {
    let active = true;
    setDetail(null);
    setDirection('');
    setError('');
    if (!sessionId || !selectedDocumentId) return () => { active = false; };
    readLore<{ document: LoreDocumentDetail }>(api.GET('/api/rpg/sessions/{session_id}/lore/document', {
      params: { path: { session_id: sessionId }, query: { document_id: selectedDocumentId } },
      cache: 'no-store',
    }))
      .then((payload) => {
        if (active) setDetail(payload.document);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Lore page could not be loaded.');
      });
    return () => { active = false; };
  }, [selectedDocumentId, sessionId]);

  const regenerateSelectedPage = async () => {
    if (!selectedDocumentId || isRegenerating) return;
    setIsRegenerating(true);
    setError('');
    try {
      const payload = await generateLore<{
        document: LoreDocumentDetail;
        lore: LoreResponse;
      }>(api.POST('/api/rpg/sessions/{session_id}/lore/regenerate', {
        params: { path: { session_id: sessionId } },
        body: { document_id: selectedDocumentId, direction: direction.trim() },
        cache: 'no-store',
      }));
      setLore(payload.lore);
      setDetail(payload.document);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Lore generation failed.');
    } finally {
      setIsRegenerating(false);
    }
  };

  const materializeLore = async (
    kind: RuntimeLoreKind,
    name: string,
    materializationDirection: string,
    documentId = '',
  ) => {
    if (!name.trim() || isMaterializing) return;
    setIsMaterializing(true);
    setError('');
    try {
      const payload = await generateLore<{
        document: LoreDocumentDetail;
        lore: LoreResponse;
      }>(api.POST('/api/rpg/sessions/{session_id}/lore/materialize', {
        params: { path: { session_id: sessionId } },
        body: { kind, name: name.trim(), direction: materializationDirection.trim(), document_id: documentId },
        cache: 'no-store',
      }));
      setLore(payload.lore);
      setDetail(payload.document);
      setSelectedId(payload.document.document_id);
      setNewLoreName('');
      setNewLoreDirection('');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Runtime lore materialization failed.');
    } finally {
      setIsMaterializing(false);
    }
  };

  const generation = lore?.generation;
  const jobs = generation?.jobs ?? [];
  const topicSummary = useMemo(() => {
    const completed = jobs.filter((job) => job.status === 'completed').length;
    return jobs.length ? `${completed}/${jobs.length} topics compiled` : 'No generation jobs recorded';
  }, [jobs]);

  if (!sessionId) {
    return <div className="rpg-journal-detail"><h3>Lore</h3><p>Select or create a campaign to browse its Campaign Bible.</p></div>;
  }
  if (error && !lore) {
    return <div className="rpg-journal-detail"><h3>Lore unavailable</h3><p>{error}</p></div>;
  }
  if (!lore) {
    return <div className="rpg-journal-detail"><h3>Loading Campaign Bible…</h3><p>Reading PostgreSQL canon and discovery state.</p></div>;
  }

  const selectedDossier = selectedEntry?.dossier;
  const title = selectedId === 'overview'
    ? 'Campaign Bible'
    : selectedDetail?.title || selectedEntry?.title || 'Campaign Bible';
  const body = selectedId === 'overview'
    ? 'Browse every known lore page and discovered dossier for this campaign. New player-safe lore for the current location is generated once when missing, committed to PostgreSQL, and reused on later visits.'
    : selectedDocumentId && !selectedDetail
      ? 'Loading selected lore page…'
      : selectedDetail?.full_text || selectedDetail?.summary_500 || selectedEntry?.summary || 'This entry has no player-visible details yet.';
  const storageLabel = lore.storage?.persisted ? 'PostgreSQL authority' : 'Portable fallback';

  return (
    <div aria-labelledby={labelledById} className="rpg-journal-grid" id={panelId} role={role}>
      <div role="group" className="rpg-journal-list" aria-label="Campaign Bible navigation">
        <article
          aria-pressed={selectedId === 'overview'}
          className={selectedId === 'overview' ? 'active' : undefined}
          onClick={() => selectEntry('overview')}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              selectEntry('overview');
            }
          }}
          role="button"
          tabIndex={0}
        >
          <span aria-hidden="true" />
          <div>
            <strong>Overview</strong>
            <p>{lore.visible_count} pages · {entries.filter((entry) => entry.kind === 'dossier').length} dossiers</p>
          </div>
        </article>

        {LORE_CATEGORY_ORDER.map((category) => {
          const rows = entries.filter((entry) => entry.category === category);
          return (
            <section aria-label={category} key={category} style={{ display: 'contents' }}>
              <div style={{ padding: '10px 12px 4px', opacity: 0.72, fontSize: 12, fontWeight: 800 }}>
                {category} <span style={{ opacity: 0.7 }}>({rows.length})</span>
              </div>
              {rows.length === 0 ? (
                <div style={{ padding: '2px 18px 8px', opacity: 0.5, fontSize: 12 }}>No known entries yet</div>
              ) : rows.map((entry) => (
                <article
                  aria-pressed={selectedId === entry.id}
                  className={selectedId === entry.id ? 'active' : undefined}
                  key={entry.id}
                  onClick={() => selectEntry(entry.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      selectEntry(entry.id);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <span aria-hidden="true" />
                  <div>
                    <strong>{entry.title}</strong>
                    <p>{statusLabel(entry.status)} · {entry.kind === 'dossier' ? 'Dossier' : 'Lore page'}</p>
                  </div>
                </article>
              ))}
            </section>
          );
        })}
      </div>

      <article className="rpg-journal-detail">
        <h3>{title}</h3>
        <p style={{ whiteSpace: 'pre-line' }}>{body}</p>
        {selectedDocumentId ? (
          <section aria-label="Lore page generation" className="rpg-lore-generation">
            <label htmlFor="rpg-lore-generation-direction">Optional generation direction</label>
            <textarea
              disabled={isRegenerating}
              id="rpg-lore-generation-direction"
              maxLength={1000}
              onChange={(event) => setDirection(event.currentTarget.value)}
              placeholder="Example: Emphasize everyday beliefs, rituals, and what the moons look like from the ground."
              rows={3}
              value={direction}
            />
            <div>
              <button
                className="rpg-secondary-button"
                disabled={isRegenerating || !selectedDetail}
                onClick={() => { void regenerateSelectedPage(); }}
                type="button"
              >
                {isRegenerating ? 'Generating richer lore…' : 'Regenerate lore'}
              </button>
              {selectedRuntimeKind ? (
                <button
                  className="rpg-secondary-button"
                  disabled={isMaterializing || !selectedDetail}
                  onClick={() => {
                    void materializeLore(
                      selectedRuntimeKind,
                      selectedEntry?.title ?? '',
                      direction,
                      selectedDocumentId,
                    );
                  }}
                  type="button"
                >
                  {isMaterializing ? 'Compiling rules & lore…' : 'Rebuild rules & lore'}
                </button>
              ) : null}
              <small>Existing canon remains the authority; your direction controls emphasis and descriptive focus.</small>
            </div>
          </section>
        ) : null}
        <div className="rpg-chip-row">
          <span>Canon r{lore.canon_revision}</span>
          <span>{lore.visible_count} known pages</span>
          <span>{lore.hidden_count} undiscovered</span>
          <span>{storageLabel}</span>
          {selectedEntry ? <span>{statusLabel(selectedEntry.status)}</span> : null}
        </div>

        {selectedId === 'overview' ? (
          <section aria-label="Campaign Bible summary" style={{ marginTop: 18 }}>
            <h4>Current campaign knowledge</h4>
            <div className="rpg-chip-row" style={{ alignItems: 'stretch', marginTop: 8 }}>
              <article style={{ minWidth: 190, padding: 10 }}>
                <strong>Current location</strong>
                <p>{lore.storage?.current_location?.name || 'Not recorded'}</p>
                <small>{lore.storage?.generated_current_location ? 'Lore generated and stored' : 'Lore already available'}</small>
              </article>
              <article style={{ minWidth: 190, padding: 10 }}>
                <strong>World Forge</strong>
                <p>{generation?.percent ?? 0}% · {topicSummary}</p>
                <small>{generation?.launch_ready ? 'World ready' : statusLabel(generation?.status ?? 'unknown')}</small>
              </article>
              <article style={{ minWidth: 190, padding: 10 }}>
                <strong>Persistence</strong>
                <p>{storageLabel}</p>
                <small>{lore.storage?.error ? `Fallback reason: ${lore.storage.error}` : 'Campaign canon is reusable across sessions.'}</small>
              </article>
            </div>
            <section aria-label="Materialize runtime lore" className="rpg-lore-materialization">
              <h4>Create a campaign discovery</h4>
              <p>Generate structured gameplay rules and matching lore together. The published world remains unchanged.</p>
              <div>
                <label htmlFor="rpg-runtime-lore-kind">Type</label>
                <select
                  disabled={isMaterializing}
                  id="rpg-runtime-lore-kind"
                  onChange={(event) => setNewLoreKind(event.currentTarget.value as RuntimeLoreKind)}
                  value={newLoreKind}
                >
                  <option value="creature">Creature</option>
                  <option value="location">Location</option>
                </select>
                <label htmlFor="rpg-runtime-lore-name">Name</label>
                <input
                  disabled={isMaterializing}
                  id="rpg-runtime-lore-name"
                  maxLength={120}
                  onChange={(event) => setNewLoreName(event.currentTarget.value)}
                  placeholder={newLoreKind === 'creature' ? 'Example: Mireglass Stag' : 'Example: The Bell-Sunk Cloister'}
                  value={newLoreName}
                />
              </div>
              <label htmlFor="rpg-runtime-lore-direction">Optional direction</label>
              <textarea
                disabled={isMaterializing}
                id="rpg-runtime-lore-direction"
                maxLength={1000}
                onChange={(event) => setNewLoreDirection(event.currentTarget.value)}
                placeholder="Describe the intended role, atmosphere, known abilities, hazards, or constraints."
                rows={3}
                value={newLoreDirection}
              />
              <button
                className="rpg-secondary-button"
                disabled={isMaterializing || !newLoreName.trim()}
                onClick={() => { void materializeLore(newLoreKind, newLoreName, newLoreDirection); }}
                type="button"
              >
                {isMaterializing ? 'Compiling discovery…' : 'Create rules & lore'}
              </button>
            </section>
          </section>
        ) : null}

        {selectedDossier && dossierFacts(selectedDossier).length > 0 ? (
          <dl style={{ display: 'grid', gap: 10, marginTop: 18 }}>
            {dossierFacts(selectedDossier).map((fact) => (
              <div key={fact.label}>
                <dt style={{ fontWeight: 800 }}>{fact.label}</dt>
                <dd style={{ margin: '3px 0 0', opacity: 0.82 }}>{fact.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        <details style={{ marginTop: 18 }}>
          <summary>World Forge generation evidence</summary>
          <ul>
            {jobs.length === 0 ? <li>No generation jobs recorded.</li> : null}
            {jobs.slice(0, 20).map((job) => (
              <li key={job.topic_id ?? `${job.generator_role}:${job.status}`}>
                {statusLabel(job.topic_id ?? job.generator_role ?? 'topic')} — {statusLabel(job.status ?? 'unknown')}
                {job.error ? `: ${job.error}` : ''}
              </li>
            ))}
          </ul>
        </details>
        {error ? <p role="alert">{error}</p> : null}
      </article>
    </div>
  );
}
