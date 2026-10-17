import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RpgWorldRail } from './RpgWorldRail';
import { npcRelationships, previewEncounter, previewJobs, previewSessionSummary, previewWorldStateRows } from './rpgUiState';
import { renderWithProviders } from '../../test/renderWithProviders';


const baseProps = {
  autoplayRunning: false,
  autoplayStatusLabel: 'Autoplay off',
  checkpointSummary: { label: 'Latest checkpoint', detail: 'checkpoint-001.json', source: 'live' as const },
  encounter: previewEncounter,
  isAutoplayPending: false,
  isCreatingCheckpoint: false,
  isRefreshingJobs: false,
  jobCards: previewJobs,
  npcRelationships,
  onCreateCheckpoint: vi.fn(),
  onRefreshJobs: vi.fn(),
  onToggleAutoplay: vi.fn(),
  reportsHref: '/api/reports',
  rpgAssets: [{ id: 'asset-1', module: 'rpg', file_name: 'checkpoint-001.json', type: 'rpg_checkpoint' }],
  rpgJobCount: 0,
  rpgReportCount: 2,
  selectedSessionSummary: previewSessionSummary,
  worldStateRows: previewWorldStateRows,
};

describe('RpgWorldRail', () => {
  it('keeps world information without restoring the Autoplay & reports section', () => {
    renderWithProviders(<RpgWorldRail {...baseProps} />);

    expect(screen.getByRole('complementary', { name: 'World, jobs, and reports' })).toBeInTheDocument();
    expect(screen.getByLabelText('Glimmerdeep Pass travel map')).toBeInTheDocument();
    expect(screen.getByText('Calendar / Season')).toBeInTheDocument();
    expect(screen.getByText('Hazards')).toBeInTheDocument();
    expect(screen.getByText('NPC relationships')).toBeInTheDocument();
    expect(screen.getByText('RPG jobs')).toBeInTheDocument();

    expect(screen.queryByText('Autoplay & reports')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Generate last 10 turn report' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open reports index' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start autoplay' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create checkpoint' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'rpg_checkpoint / rpg' })).toBeInTheDocument();
  });

  it('keeps job and compact runtime controls active', () => {
    const onRefreshJobs = vi.fn();
    const onCreateCheckpoint = vi.fn();
    const onToggleAutoplay = vi.fn();
    renderWithProviders(
      <RpgWorldRail
        {...baseProps}
        onCreateCheckpoint={onCreateCheckpoint}
        onRefreshJobs={onRefreshJobs}
        onToggleAutoplay={onToggleAutoplay}
        rpgJobCount={1}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Refresh RPG jobs' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start autoplay' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create checkpoint' }));
    expect(onRefreshJobs).toHaveBeenCalledTimes(1);
    expect(onToggleAutoplay).toHaveBeenCalledTimes(1);
    expect(onCreateCheckpoint).toHaveBeenCalledTimes(1);
    expect(screen.getByText('1 live')).toBeInTheDocument();
  });

  it('shows at most three RPG job cards and preserves failure details', () => {
    renderWithProviders(
      <RpgWorldRail
        {...baseProps}
        jobCards={[
          { id: 'job-1', title: 'rpg.turn.1', status: 'Completed', progress: 100, detail: 'First job', source: 'live' },
          { id: 'job-2', title: 'rpg.turn.2', status: 'Completed', progress: 100, detail: 'Second job', source: 'live' },
          {
            id: 'job-3',
            title: 'rpg.report.last10',
            status: 'failed',
            progress: 66,
            detail: 'Report job retained in job history',
            errorDetail: 'Last 10 turn report failed before ZIP creation.',
            source: 'live',
          },
          { id: 'job-4', title: 'rpg.turn.4', status: 'Queued', progress: 0, detail: 'Fourth job', source: 'live' },
        ]}
        rpgJobCount={4}
      />,
    );

    expect(screen.getByText('4 live')).toBeInTheDocument();
    expect(screen.getByText('rpg.turn.1')).toBeInTheDocument();
    expect(screen.getByText('rpg.turn.2')).toBeInTheDocument();
    expect(screen.getByText('rpg.report.last10')).toBeInTheDocument();
    expect(screen.getByText('Reason: Last 10 turn report failed before ZIP creation.')).toBeInTheDocument();
    expect(screen.queryByText('rpg.turn.4')).not.toBeInTheDocument();
  });
});
