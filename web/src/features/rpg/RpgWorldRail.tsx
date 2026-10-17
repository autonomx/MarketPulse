import { Progress, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { rpgSessionClient } from './api/rpgSessionClient';
import { RpgMapDialog } from './RpgMapDialog';
import type {
  RpgCheckpointSummaryPreview,
  RpgEncounterPreview,
  RpgJobCardPreview,
  RpgNpcRelationshipPreview,
  RpgSessionSummaryPreview,
  RpgWorldStateRowPreview,
} from './rpgUiState';
import './RpgVisualAssets.css';

const MAP_ART_SRC = '/rpg/glimmerdeep-pass-map.svg';

interface RpgReportAssetPreview {
  id: unknown;
  module?: unknown;
  file_name?: unknown;
  type?: unknown;
}

interface RpgWorldRailProps {
  autoplayRunning: boolean;
  autoplayStatusLabel: string;
  className?: string;
  checkpointControlStatus?: string;
  checkpointSummary: RpgCheckpointSummaryPreview;
  currentMapId?: string | null;
  encounter: RpgEncounterPreview;
  isAutoplayPending: boolean;
  isCreatingCheckpoint: boolean;
  isRefreshingJobs: boolean;
  jobCards: RpgJobCardPreview[];
  npcRelationships: RpgNpcRelationshipPreview[];
  onCreateCheckpoint: () => void;
  onRefreshJobs: () => void;
  onToggleAutoplay: () => void;
  reportsHref: string;
  rpgAssets: RpgReportAssetPreview[];
  rpgJobCount: number;
  rpgReportCount: number;
  selectedSessionSummary: RpgSessionSummaryPreview;
  worldStateRows: RpgWorldStateRowPreview[];
}

export function RpgWorldRail({
  autoplayRunning,
  autoplayStatusLabel,
  className,
  checkpointControlStatus,
  checkpointSummary,
  currentMapId,
  encounter,
  isAutoplayPending,
  isCreatingCheckpoint,
  isRefreshingJobs,
  jobCards,
  npcRelationships,
  onCreateCheckpoint,
  onRefreshJobs,
  onToggleAutoplay,
  rpgAssets,
  rpgJobCount,
  selectedSessionSummary,
  worldStateRows,
}: RpgWorldRailProps) {
  const railClassName = className ? `rpg-right-rail ${className}` : 'rpg-right-rail';
  const isPreview = selectedSessionSummary.source === 'preview';
  const liveSessionQuery = useQuery({
    queryKey: ['feature', 'rpg', 'session', selectedSessionSummary.id],
    queryFn: () => rpgSessionClient.getRpgSession(selectedSessionSummary.id),
    enabled: !isPreview && Boolean(selectedSessionSummary.id.trim()),
  });
  const sessionRecord = recordValue(liveSessionQuery.data?.session);
  const stateRecord = recordValue(sessionRecord.state);
  const mapStateRecord = recordValue(stateRecord.map_state);
  const queriedMapId = typeof mapStateRecord.current_map_id === 'string' ? mapStateRecord.current_map_id.trim() : '';
  const activeMapId = currentMapId?.trim() || queriedMapId;
  const visibleJobCards = jobCards.slice(0, 3);
  const visibleAssets = rpgAssets.slice(0, 3);
  const canOpenLiveMap = !isPreview && Boolean(selectedSessionSummary.id.trim() && activeMapId);
  const [isMapOpen, setIsMapOpen] = useState(false);

  return (
    <>
      <aside className={railClassName} aria-label="World, jobs, and reports">
        <section className="rpg-card rpg-map-card">
          <div className="rpg-section-heading">
            <p className="eyebrow">World & location</p>
          </div>
          <div role="img" className={isPreview ? 'rpg-map-preview rpg-map-preview-has-image' : 'rpg-map-preview'} aria-label={`${selectedSessionSummary.location} travel map`}>
            {isPreview ? (
              <img className="rpg-map-image" src={MAP_ART_SRC} alt="" aria-hidden="true" loading="lazy" />
            ) : (
              <span className="rpg-live-visual-label">{activeMapId ? 'Live map ready' : 'Live map unavailable'}</span>
            )}
            <span className="rpg-map-pin" aria-hidden="true" />
          </div>
          <strong>{selectedSessionSummary.location}</strong>
          {isPreview ? <small>Preview artwork</small> : <small>{activeMapId || 'Canonical map state is not available.'}</small>}
          <button className="rpg-secondary-button" disabled={!canOpenLiveMap} onClick={() => setIsMapOpen(true)} type="button">
            Open interactive map
          </button>
        </section>

        <section className="rpg-card rpg-world-grid-card">
          <div className="rpg-world-state">
            <p className="eyebrow">World state</p>
            {worldStateRows.map((row) => (
              <div className="rpg-world-state-row" key={row.label}>
                <span aria-hidden="true">{row.icon}</span>
                <span>{row.label}</span>
                <strong>{row.value}</strong>
              </div>
            ))}
          </div>
          <div role="group" className="rpg-encounter-card" aria-label={`${encounter.title} encounter state`}>
            <p className="eyebrow">Encounter</p>
            <span aria-hidden="true">{encounter.icon}</span>
            <strong>{encounter.title}</strong>
            <p>{encounter.detail}</p>
            <small>{encounter.source === 'live' ? 'Live encounter state' : 'Preview encounter state'}</small>
          </div>
        </section>

        <section className="rpg-card">
          <p className="eyebrow">NPC relationships</p>
          <div className="rpg-list-stack">
            {npcRelationships.length ? (
              npcRelationships.map((npc) => (
                <article className="rpg-relationship-row" key={npc.name}>
                  <span className="rpg-avatar rpg-avatar-small" aria-hidden="true">{npc.name[0]}</span>
                  <strong>{npc.name}</strong>
                  <small>{npc.stance}</small>
                  <span className="rpg-party-health"><span style={{ width: `${npc.score}%` }} /></span>
                  <small>{npc.score}</small>
                </article>
              ))
            ) : (
              <p className="rpg-empty-state">No NPC relationships recorded.</p>
            )}
          </div>
        </section>

        <section className="rpg-card rpg-jobs-card">
          <div className="rpg-section-heading">
            <p className="eyebrow">RPG jobs</p>
            <button type="button" onClick={onRefreshJobs} disabled={isRefreshingJobs}>
              {isRefreshingJobs ? 'Refreshing…' : 'Refresh RPG jobs'}
            </button>
          </div>
          <span>{rpgJobCount ? `${rpgJobCount} live` : isPreview ? 'Preview' : 'No active RPG jobs'}</span>
          <div className="rpg-list-stack">
            {visibleJobCards.length ? (
              visibleJobCards.map((job) => (
                <article className="rpg-job-row" key={job.id}>
                  <div>
                    <strong>{job.title}</strong>
                    <small>{job.source === 'live' ? job.status : `${job.status} preview`}</small>
                  </div>
                  <Progress value={job.progress} aria-label={`${job.title} progress`} />
                  <Text size="xs">{job.detail}</Text>
                  {job.errorDetail ? <Text className="rpg-job-error" size="xs">Reason: {job.errorDetail}</Text> : null}
                </article>
              ))
            ) : (
              <p className="rpg-empty-state">No RPG jobs are currently queued or running.</p>
            )}
          </div>
          <div role="group" className="rpg-survival-actions" aria-label="RPG runtime tools">
            <button className="rpg-secondary-button" type="button" onClick={onToggleAutoplay} disabled={isAutoplayPending}>
              {isAutoplayPending ? 'Updating autoplay…' : autoplayRunning ? 'Stop autoplay' : 'Start autoplay'}
            </button>
            <button className="rpg-secondary-button" type="button" onClick={onCreateCheckpoint} disabled={isCreatingCheckpoint}>
              {isCreatingCheckpoint ? 'Creating checkpoint…' : 'Create checkpoint'}
            </button>
          </div>
          <small>{autoplayStatusLabel} · {checkpointSummary.label}: {checkpointSummary.detail}</small>
          {checkpointControlStatus ? <small>{checkpointControlStatus}</small> : null}
          {visibleAssets.map((asset) => (
            <article className="rpg-job-row" key={String(asset.id)}>
              <div>
                <h3>{String(asset.type)} / {String(asset.module)}</h3>
                <small>{String(asset.file_name || asset.id)}</small>
              </div>
            </article>
          ))}
        </section>
      </aside>
      {canOpenLiveMap && activeMapId ? (
        <RpgMapDialog
          locationLabel={selectedSessionSummary.location}
          mapId={activeMapId}
          onClose={() => setIsMapOpen(false)}
          open={isMapOpen}
          sessionId={selectedSessionSummary.id}
        />
      ) : null}
    </>
  );
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
