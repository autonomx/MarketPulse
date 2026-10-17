import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { ApiTimeoutError, omnixApiClient, type JobRecord } from '../../api/client';
import { rpgSessionClient, type RpgLoadoutActionRequest, type RpgNewGameRequest } from './api/rpgSessionClient';
import { getHermesRpgExecutionLedger, type HermesRpgApprovedFlowResponse } from './api/hermesRpgApprovedFlowClient';
import {
  getHermesRouteDecision,
  getHermesRpgSuggestions,
  readHermesRpgTurn,
  type HermesRpgSuggestion,
} from './api/hermesClient';
import { checkHermesRpgSequence } from './api/hermesRpgSequenceClient';
import type { OmnixModuleDefinition } from '../../app/modules';
import { WorkspacePanel } from '../../design/primitives';
import { FeatureSubmitFeedback, FeatureValidationMessage } from '../../shared/FeatureSubmitFeedback';
import { RpgActionComposer } from './RpgActionComposer';
import { RpgCombatSurface } from './RpgCombatSurface';
import { RpgCreateCampaignWizard } from './RpgCreateCampaignWizard';
import { RpgHermesExecutionHistory } from './RpgHermesExecutionHistory';
import { RpgHermesExecutionResult } from './RpgHermesExecutionResult';
import { RpgHermesSequenceJobPanel } from './RpgHermesSequenceJobPanel';
import { RpgHermesSequenceReviewPanel } from './RpgHermesSequenceReviewPanel';
import { RpgLiveDataStatus, type RpgLiveDataStatusCard } from './RpgLiveDataStatus';
import { RpgLoadoutTabs } from './RpgLoadoutTabs';
import { RpgNarrativeTabs } from './RpgNarrativeTabs';
import { RpgPlayerRail } from './RpgPlayerRail';
import { RpgStoryScene } from './RpgStoryScene';
import { RpgWorkspaceHeader } from './RpgWorkspaceHeader';
import { RpgWorldRail } from './RpgWorldRail';
import { createRpgCombatSurfaceState } from './rpgCombatState';
import { rpgAssistStateFromItems } from './rpgAssistState';
import { hermesSequencePreviewModel } from './hermesSequencePreviewModel';
import { buildHermesSequenceReviewRequest } from './hermesSequenceReviewRequest';
import { createRpgTurnReadoutPreview } from './rpgTurnReadoutState';
import {
  createRpgWorkspaceState,
  type RpgQuickActionPreview,
  type RpgStoryMessagePreview,
  rpgTurnOutputText,
} from './rpgUiState';
import './RpgWorkspace.css';
import './RpgResponsivePolish.css';

interface RpgFormValues {
  sessionId: string;
  command: string;
}

interface PendingRpgTurnSubmission {
  command: string;
  sessionId: string;
  submittedAt: number;
}

const ACTIVE_JOB_STATUSES = new Set(['queued', 'leased', 'running', 'waiting', 'retrying', 'cancel_requested']);
const RPG_SELECTED_SESSION_STORAGE_KEY = 'omnix:rpg:selected-session-id';
const RPG_TURN_QUEUE_TIMEOUT_MS = 45_000;
const RPG_TURN_RECOVERY_WINDOW_MS = 60_000;

function formatQueryError(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }

  return 'Request failed before the RPG workspace could read this source.';
}

function readStoredRpgSessionId(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  try {
    return window.localStorage.getItem(RPG_SELECTED_SESSION_STORAGE_KEY)?.trim() ?? '';
  } catch {
    return '';
  }
}

function writeStoredRpgSessionId(sessionId: string | null): void {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    if (sessionId?.trim()) {
      window.localStorage.setItem(RPG_SELECTED_SESSION_STORAGE_KEY, sessionId.trim());
    } else {
      window.localStorage.removeItem(RPG_SELECTED_SESSION_STORAGE_KEY);
    }
  } catch {
    // Storage can be unavailable in private or embedded contexts; the live selection still works in memory.
  }
}

function timestampMs(value?: string | null): number {
  const parsed = Date.parse(value ?? '');
  return Number.isFinite(parsed) ? parsed : 0;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

const EMPTY_VISIBLE_RESPONSE_TEXT = new Set(['', '[]', '{}', '[ ]', '{ }', 'null', 'none', 'false', 'true']);

function isEmptyVisibleResponseText(text: string): boolean {
  return EMPTY_VISIBLE_RESPONSE_TEXT.has(text.trim().toLowerCase());
}

function cleanSubmittedTurnResponse(response: string | null): string | null {
  if (!response) return null;
  const paragraphs = response.split(/\n\s*\n/).map((paragraph) => paragraph.trim()).filter(Boolean);
  const visibleParagraphs = paragraphs.filter((paragraph) => !/^(?:Action|Result):\s*/i.test(paragraph));
  const text = (visibleParagraphs.length ? visibleParagraphs : paragraphs).join('\n\n');
  return text && !isEmptyVisibleResponseText(text) ? text : null;
}

function submittedTurnResponseContent(job: JobRecord | undefined): string | null {
  const output = arrayValue(job?.output_refs)
    .map(recordValue)
    .find((candidate) => firstString(candidate.type, candidate.kind) === 'rpg_turn_response');
  return cleanSubmittedTurnResponse(rpgTurnOutputText(output) ?? null);
}

function submittedTurnCommand(job: JobRecord | undefined): string | null {
  return firstString(recordValue(job?.input_payload).command);
}

function submittedTurnSessionId(job: JobRecord | undefined): string | null {
  return firstString(recordValue(job?.input_ref).session_id);
}

function inferSubmittedResponseSpeaker(response: string): string {
  const match = response.match(/^\s*([A-Z][A-Za-z0-9 _'-]{0,40}):/);
  return match?.[1]?.trim() || 'Omnix';
}

export function quickActionsFromHermesSuggestions(
  suggestions: HermesRpgSuggestion[],
): RpgQuickActionPreview[] {
  const icons: Record<string, string> = {
    combat: '⚔',
    dialogue: '☯',
    inventory: '▣',
    journal: '◇',
    objective: '◆',
    progression: '✦',
    travel: '⌖',
  };
  return suggestions.flatMap((suggestion) => {
    const command = suggestion.command?.trim();
    if (!command) return [];
    return [{
      command,
      icon: icons[suggestion.kind ?? ''] ?? '◇',
      label: suggestion.label?.trim() || command,
    }];
  }).slice(0, 6);
}

function isRpgTurnJobType(type: string): boolean {
  return type === 'rpg.turn' || type === 'rpg.turn.foreground_record';
}

function submittedTurnInteractionId(job: JobRecord): string | undefined {
  for (const output of job.output_refs ?? []) {
    const record = recordValue(output);
    const turnResponse = recordValue(record.turn_response);
    const interactionId = firstString(turnResponse.interaction_id, record.interaction_id);
    if (interactionId) return interactionId;
  }
  return undefined;
}

export function buildSubmittedTurnStoryMessages(
  job: JobRecord | undefined,
  heroName: string,
  heroAvatar: string,
  selectedSessionId: string | null,
): RpgStoryMessagePreview[] {
  if (!job || !isRpgTurnJobType(job.type)) return [];
  // Foreground-record jobs mirror the same structured response already owned
  // by the turn UI store and durable session. Rendering their combined content
  // creates a transient second response and can surface stale fallback prose.
  if (job.type === 'rpg.turn.foreground_record') return [];
  const jobSessionId = submittedTurnSessionId(job);
  if (selectedSessionId && jobSessionId && jobSessionId !== selectedSessionId) return [];

  const messages: RpgStoryMessagePreview[] = [];
  const interactionId = submittedTurnInteractionId(job);
  const command = submittedTurnCommand(job);
  if (command) {
    messages.push({
      id: interactionId ? `${interactionId}:player` : undefined,
      interactionId,
      avatar: heroAvatar,
      speaker: `${heroName} (You)`,
      text: command,
      tone: 'player',
    });
  }

  const response = submittedTurnResponseContent(job);
  if (response) {
    const speaker = inferSubmittedResponseSpeaker(response);
    messages.push({
      id: interactionId ? `${interactionId}:submitted-response` : undefined,
      interactionId,
      avatar: speaker === 'Omnix' ? 'O' : speaker.charAt(0).toUpperCase(),
      speaker: speaker === 'Omnix' ? 'Omnix (Narrator)' : speaker,
      text: response,
      tone: speaker === 'Omnix' ? 'narrator' : 'npc',
    });
  }
  return messages;
}

export function RpgWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const queryClient = useQueryClient();
  const [isPlayerRailCollapsed, setIsPlayerRailCollapsed] = useState(false);
  const [isWorldRailCollapsed, setIsWorldRailCollapsed] = useState(false);
  const [isLiveDataExpanded, setIsLiveDataExpanded] = useState(false);
  const [campaignMenuHost, setCampaignMenuHost] = useState<HTMLElement | null>(null);
  const [latestHermesExecutionResult, setLatestHermesExecutionResult] = useState<HermesRpgApprovedFlowResponse | null>(null);
  const [hermesAssistMode, setHermesAssistMode] = useState('review_each_step');
  const inventoryQuery = useQuery({
    queryKey: ['feature', 'rpg', 'replay-inventory'],
    queryFn: () => rpgSessionClient.listRpgSessionSummaries(),
  });
  const jobsQuery = useQuery({
    queryKey: ['platform', 'jobs'],
    queryFn: () => omnixApiClient.listJobs(),
    refetchInterval: 3000,
  });
  const assetsQuery = useQuery({
    queryKey: ['platform', 'assets'],
    queryFn: () => omnixApiClient.listAssets(),
  });
  const reportsQuery = useQuery({
    queryKey: ['platform', 'reports'],
    queryFn: () => omnixApiClient.listReports(),
  });
  const trustedUnindexedSessionIdsRef = useRef<Set<string>>(new Set());
  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors },
  } = useForm<RpgFormValues>({
    defaultValues: { sessionId: readStoredRpgSessionId(), command: '' },
  });
  const selectedSessionId = watch('sessionId');
  const summaryState = createRpgWorkspaceState({
    inventory: inventoryQuery.data,
    jobs: jobsQuery.data,
    assets: assetsQuery.data,
    reports: reportsQuery.data,
    selectedSessionId,
  });
  const requestedSessionId = selectedSessionId?.trim() ?? '';
  const selectedSessionIsIndexed = Boolean(
    requestedSessionId && summaryState.sessionSummaries.some((session) => session.source === 'live' && session.id === requestedSessionId),
  );
  const fallbackLiveSessionId = summaryState.sessionSummaries.find((session) => session.source === 'live')?.id ?? null;
  const selectedSummarySessionId = requestedSessionId || fallbackLiveSessionId;
  useEffect(() => {
    if (!requestedSessionId && fallbackLiveSessionId) {
      setValue('sessionId', fallbackLiveSessionId, { shouldValidate: true });
    }
  }, [fallbackLiveSessionId, requestedSessionId, setValue]);
  const selectedSessionQuery = useQuery({
    queryKey: ['feature', 'rpg', 'session', selectedSummarySessionId],
    queryFn: () => rpgSessionClient.getRpgSession(selectedSummarySessionId ?? ''),
    enabled: Boolean(selectedSummarySessionId),
  });
  useEffect(() => {
    if (!requestedSessionId || !inventoryQuery.data || selectedSessionIsIndexed || !selectedSessionQuery.isError) {
      return;
    }
    if (trustedUnindexedSessionIdsRef.current.has(requestedSessionId)) {
      return;
    }

    setValue('sessionId', fallbackLiveSessionId ?? '', { shouldValidate: true });
  }, [
    fallbackLiveSessionId,
    inventoryQuery.data,
    requestedSessionId,
    selectedSessionIsIndexed,
    selectedSessionQuery.isError,
    setValue,
  ]);
  const {
    heroSummary,
    heroStats,
    survival,
    equippedGear,
    partyMembers,
    activeQuests,
    quickActions,
    storyMessages,
    recentEvents,
    journalEntries,
    narrativeLogEntries,
    journalDetail,
    inventoryItems,
    hotbarAbilities,
    worldStateRows,
    npcRelationships,
    encounter,
    sessionSummaries,
    selectedSessionSummary,
    checkpointSummary,
    rpgJobs,
    rpgAssets,
    rpgReports,
    jobCards,
  } = createRpgWorkspaceState({
    inventory: inventoryQuery.data,
    jobs: jobsQuery.data,
    assets: assetsQuery.data,
    reports: reportsQuery.data,
    selectedSessionId,
    selectedSession: selectedSessionQuery.data?.session,
  });
  const combatSurface = createRpgCombatSurfaceState({ encounter, heroSummary, partyMembers });
  const selectedLiveSessionId = selectedSessionSummary.source === 'live' ? selectedSessionSummary.id : null;
  const selectedLiveSessionIsIndexed = Boolean(
    selectedLiveSessionId && sessionSummaries.some((session) => session.source === 'live' && session.id === selectedLiveSessionId),
  );
  const hermesSuggestionsQuery = useQuery({
    queryKey: ['feature', 'rpg', 'hermes-suggestions', selectedLiveSessionId],
    queryFn: () => getHermesRpgSuggestions({ session_id: selectedLiveSessionId ?? '' }),
    enabled: Boolean(selectedLiveSessionId),
  });
  const hermesSuggestions = hermesSuggestionsQuery.data?.suggestions ?? [];
  const dynamicQuickActions = quickActionsFromHermesSuggestions(hermesSuggestions);
  const responseOptions = dynamicQuickActions.length ? dynamicQuickActions : quickActions;
  const hermesSequenceReviewRequest = buildHermesSequenceReviewRequest({
    assistMode: hermesAssistMode,
    quickActions: responseOptions,
    selectedSessionSummary,
    suggestions: hermesSuggestions,
  });
  const hermesSequenceReviewMutation = useMutation({
    mutationFn: () => checkHermesRpgSequence(hermesSequenceReviewRequest),
  });
  const hermesSequencePreview = hermesSequencePreviewModel(hermesSequenceReviewMutation.data);
  const hermesExecutionLedgerQuery = useQuery({
    queryKey: ['feature', 'rpg', 'hermes-execution-ledger', selectedLiveSessionId],
    queryFn: () => getHermesRpgExecutionLedger({ limit: 10, sessionId: selectedLiveSessionId ?? undefined }),
    enabled: Boolean(selectedLiveSessionId),
  });
  const hermesSuggestionState = selectedLiveSessionId
    ? rpgAssistStateFromItems(
      hermesSuggestions,
      hermesSuggestionsQuery.isPending,
      hermesSuggestionsQuery.isError || hermesSuggestionsQuery.data?.ok === false,
    )
    : 'idle';
  const hermesRouteDecisionQuery = useQuery({
    queryKey: ['feature', 'rpg', 'hermes-route-decision', 'rpg'],
    queryFn: () => getHermesRouteDecision('rpg'),
  });
  const hermesRouteDecision = hermesRouteDecisionQuery.data?.ok
    ? {
      mode: hermesRouteDecisionQuery.data.mode ?? 'rpg',
      hermesRole: hermesRouteDecisionQuery.data.role ?? 'suggest',
      owner: hermesRouteDecisionQuery.data.owner ?? 'rpg_sim',
      reviewRequired: Boolean(hermesRouteDecisionQuery.data.review_required),
      boundary: hermesRouteDecisionQuery.data.boundary ?? 'RPG simulation validates truth before state is accepted.',
    }
    : undefined;
  useEffect(() => {
    if (selectedLiveSessionId && selectedLiveSessionIsIndexed) {
      writeStoredRpgSessionId(selectedLiveSessionId);
      return;
    }

    if (inventoryQuery.data && !fallbackLiveSessionId) {
      writeStoredRpgSessionId(null);
    }
  }, [fallbackLiveSessionId, inventoryQuery.data, selectedLiveSessionId, selectedLiveSessionIsIndexed]);
  const refreshedTurnJobRef = useRef<string | null>(null);
  const pendingTurnSubmissionRef = useRef<PendingRpgTurnSubmission | null>(null);
  const latestCompletedTurnJob = rpgJobs
    .filter((job) => {
      const sessionId = typeof job.input_ref?.session_id === 'string' ? job.input_ref.session_id : null;
      return isRpgTurnJobType(job.type) && job.status === 'completed' && sessionId === selectedLiveSessionId;
    })
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0];
  const latestCompletedTurnJobId = latestCompletedTurnJob?.id;
  const hermesTurnReadoutQuery = useQuery({
    queryKey: ['feature', 'rpg', 'hermes-turn-readout', selectedLiveSessionId, latestCompletedTurnJobId],
    queryFn: () => readHermesRpgTurn({ session_id: selectedLiveSessionId ?? '' }),
    enabled: Boolean(selectedLiveSessionId),
  });
  const hermesTurnReadout = createRpgTurnReadoutPreview(hermesTurnReadoutQuery.data);
  const refetchSelectedSession = selectedSessionQuery.refetch;
  const refetchInventory = inventoryQuery.refetch;
  useEffect(() => {
    if (!latestCompletedTurnJobId || refreshedTurnJobRef.current === latestCompletedTurnJobId) return;
    refreshedTurnJobRef.current = latestCompletedTurnJobId;
    void Promise.all([
      refetchSelectedSession(),
      refetchInventory(),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-suggestions', selectedLiveSessionId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-route-decision', 'rpg'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-turn-readout', selectedLiveSessionId] }),
    ]);
  }, [latestCompletedTurnJobId, queryClient, refetchInventory, refetchSelectedSession, selectedLiveSessionId]);
  const activeAutoplayJob = rpgJobs.find((job) => job.type === 'rpg.autoplay' && ACTIVE_JOB_STATUSES.has(job.status));
  const hasLiveSessions = (inventoryQuery.data?.sessions?.length ?? 0) > 0;
  const liveDataStatusCards: RpgLiveDataStatusCard[] = [
    {
      id: 'sessions',
      label: 'Sessions',
      state: inventoryQuery.isError
        ? 'error'
        : inventoryQuery.isPending && !inventoryQuery.data
          ? 'loading'
          : inventoryQuery.isFetching && inventoryQuery.data
            ? 'refreshing'
            : hasLiveSessions
              ? 'ready'
              : 'empty',
      detail: inventoryQuery.isError
        ? formatQueryError(inventoryQuery.error)
        : inventoryQuery.isPending && !inventoryQuery.data
          ? 'Loading replay persistence inventory and campaign sessions.'
          : inventoryQuery.isFetching && inventoryQuery.data
            ? 'Refreshing the selected session and checkpoint metadata.'
            : hasLiveSessions
              ? `${inventoryQuery.data?.sessions?.length ?? 0} saved session${(inventoryQuery.data?.sessions?.length ?? 0) === 1 ? '' : 's'} available.`
              : 'No saved RPG sessions found. Preview fallback remains active until a campaign is created.',
    },
    {
      id: 'jobs',
      label: 'Jobs',
      state: jobsQuery.isError
        ? 'error'
        : jobsQuery.isPending && !jobsQuery.data
          ? 'loading'
          : jobsQuery.isFetching && jobsQuery.data
            ? 'refreshing'
            : rpgJobs.length
              ? 'ready'
              : 'empty',
      detail: jobsQuery.isError
        ? formatQueryError(jobsQuery.error)
        : jobsQuery.isPending && !jobsQuery.data
          ? 'Loading shared job queue state for RPG turns and autoplay.'
          : jobsQuery.isFetching && jobsQuery.data
            ? 'Polling background RPG jobs.'
            : rpgJobs.length
              ? `${rpgJobs.length} RPG job${rpgJobs.length === 1 ? '' : 's'} visible in the workspace.`
              : 'No live RPG jobs. Preview job cards keep the rail layout stable.',
    },
    {
      id: 'checkpoints',
      label: 'Checkpoints',
      state: assetsQuery.isError
        ? 'error'
        : assetsQuery.isPending && !assetsQuery.data
          ? 'loading'
          : assetsQuery.isFetching && assetsQuery.data
            ? 'refreshing'
            : rpgAssets.length
              ? 'ready'
              : 'empty',
      detail: assetsQuery.isError
        ? formatQueryError(assetsQuery.error)
        : assetsQuery.isPending && !assetsQuery.data
          ? 'Loading RPG checkpoint and report assets.'
          : assetsQuery.isFetching && assetsQuery.data
            ? 'Refreshing asset metadata for checkpoint/report links.'
            : rpgAssets.length
              ? `${rpgAssets.length} RPG asset${rpgAssets.length === 1 ? '' : 's'} found for checkpoints or reports.`
              : 'No RPG checkpoint/report assets found yet.',
    },
    {
      id: 'reports',
      label: 'Reports',
      state: reportsQuery.isError
        ? 'error'
        : reportsQuery.isPending && !reportsQuery.data
          ? 'loading'
          : reportsQuery.isFetching && reportsQuery.data
            ? 'refreshing'
            : rpgReports.length
              ? 'ready'
              : 'empty',
      detail: reportsQuery.isError
        ? formatQueryError(reportsQuery.error)
        : reportsQuery.isPending && !reportsQuery.data
          ? 'Loading generated report index.'
          : reportsQuery.isFetching && reportsQuery.data
            ? 'Refreshing RPG report availability.'
            : rpgReports.length
              ? `${rpgReports.length} RPG report${rpgReports.length === 1 ? '' : 's'} ready to open.`
              : 'No RPG reports found. Run autoplay or export a report to populate this source.',
    },
  ];
  const dashboardClassName = [
    'rpg-dashboard-grid',
    isPlayerRailCollapsed ? 'rpg-dashboard-grid-left-collapsed' : '',
    isWorldRailCollapsed ? 'rpg-dashboard-grid-right-collapsed' : '',
  ]
    .filter(Boolean)
    .join(' ');
  const invalidateRpgWorkspaceQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'replay-inventory'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-suggestions'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-route-decision'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-turn-readout'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-execution-ledger'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-sequence-state'] }),
      queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }),
      queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] }),
      queryClient.invalidateQueries({ queryKey: ['platform', 'reports'] }),
    ]);
  };
  const createJobMutation = useMutation({
    mutationFn: (values: RpgFormValues) => {
      const sessionId = values.sessionId || selectedLiveSessionId;
      return rpgSessionClient.submitTurnJob(
        {
          module: 'rpg',
          type: 'rpg.turn',
          resource_class: 'gpu:llm',
          priority: 0,
          input_ref: sessionId ? { session_id: sessionId } : null,
          input_payload: {
            command: values.command,
            determinism_policy: 'replay_preserving',
          },
          stages: [
            { id: 'load-session', label: 'Load session', resource_class: 'cpu', status: 'queued' },
            { id: 'apply-turn', label: 'Apply deterministic turn', resource_class: 'cpu', status: 'queued' },
            { id: 'narrate', label: 'Generate narration', resource_class: 'gpu:llm', status: 'queued' },
            { id: 'checkpoint', label: 'Write checkpoint', resource_class: 'cpu', status: 'queued' },
          ],
        },
        {
          timeoutMs: RPG_TURN_QUEUE_TIMEOUT_MS,
          timeoutMessage:
            'The RPG turn queue request is taking longer than expected. The workspace will keep checking the RPG job queue for this turn.',
        },
      );
    },
    onMutate: (values) => {
      pendingTurnSubmissionRef.current = {
        command: values.command.trim(),
        sessionId: values.sessionId || selectedLiveSessionId || '',
        submittedAt: Date.now(),
      };
    },
    onSuccess: (_job, values) => {
      const sessionId = values.sessionId || selectedLiveSessionId || '';
      if (sessionId) {
        writeStoredRpgSessionId(sessionId);
      }
      reset({ sessionId, command: '' });
      void invalidateRpgWorkspaceQueries();
    },
  });
  const activeSubmittedTurnJobId = createJobMutation.data?.id ?? null;
  const submittedTurnJobQuery = useQuery({
    queryKey: ['platform', 'jobs', 'submitted-rpg-turn', activeSubmittedTurnJobId],
    queryFn: () => omnixApiClient.getJob(activeSubmittedTurnJobId ?? ''),
    enabled: Boolean(activeSubmittedTurnJobId),
    refetchInterval: activeSubmittedTurnJobId ? 1500 : false,
  });
  const submittedTurnJobFromQuery = submittedTurnJobQuery.data;
  useEffect(() => {
    if (
      !submittedTurnJobFromQuery || !isRpgTurnJobType(submittedTurnJobFromQuery.type)
      || submittedTurnJobFromQuery.status !== 'completed'
      || refreshedTurnJobRef.current === submittedTurnJobFromQuery.id
    ) {
      return;
    }
    refreshedTurnJobRef.current = submittedTurnJobFromQuery.id;
    void Promise.all([
      refetchSelectedSession(),
      refetchInventory(),
      jobsQuery.refetch(),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-suggestions', selectedLiveSessionId] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-route-decision', 'rpg'] }),
      queryClient.invalidateQueries({ queryKey: ['feature', 'rpg', 'hermes-turn-readout', selectedLiveSessionId] }),
    ]);
  }, [jobsQuery, queryClient, refetchInventory, refetchSelectedSession, selectedLiveSessionId, submittedTurnJobFromQuery]);
  const createCheckpointMutation = useMutation({
    mutationFn: () =>
      omnixApiClient.createReplayCheckpoint({
        source: 'rpg-workspace',
        version: 'rpg-ui-control-v1',
        metadata: {
          module: 'rpg',
          session_id: selectedLiveSessionId,
          session_title: selectedSessionSummary.title,
          reason: 'manual-ui-checkpoint',
        },
        payload: {
          selected_session_id: selectedLiveSessionId,
          title: selectedSessionSummary.title,
          location: selectedSessionSummary.location,
          turn_label: selectedSessionSummary.turnLabel,
          checkpoint_label: selectedSessionSummary.checkpointLabel,
        },
      }),
    onSuccess: () => {
      void invalidateRpgWorkspaceQueries();
    },
  });
  const loadoutActionMutation = useMutation({
    mutationFn: ({ sessionId, request }: { sessionId: string; request: RpgLoadoutActionRequest }) => rpgSessionClient.applyRpgLoadoutAction(sessionId, request),
    onSuccess: async () => {
      await invalidateRpgWorkspaceQueries();
    },
  });
  const createCampaignMutation = useMutation({
    mutationFn: (request: RpgNewGameRequest) => rpgSessionClient.createRpgNewGame(request),
    onSuccess: (result) => {
      if (result.ok && result.session_id) {
        trustedUnindexedSessionIdsRef.current.add(result.session_id);
        if (result.session) {
          queryClient.setQueryData(['feature', 'rpg', 'session', result.session_id], result);
        }
        writeStoredRpgSessionId(result.session_id);
        setValue('sessionId', result.session_id, { shouldDirty: true, shouldValidate: true });
      }
      void invalidateRpgWorkspaceQueries();
    },
  });
  const autoplayMutation = useMutation({
    mutationFn: () => {
      if (activeAutoplayJob) {
        return omnixApiClient.cancelJob(activeAutoplayJob.id, 'Stopped from the RPG workspace autoplay control.');
      }

      return omnixApiClient.createJob({
        module: 'rpg',
        type: 'rpg.autoplay',
        resource_class: 'gpu:llm',
        priority: 0,
        input_ref: selectedLiveSessionId ? { session_id: selectedLiveSessionId } : null,
        input_payload: {
          determinism_policy: 'replay_preserving',
          source: 'rpg-workspace',
          turn_budget: 10,
        },
        stages: [
          { id: 'load-session', label: 'Load RPG session', resource_class: 'cpu', status: 'queued' },
          { id: 'plan-turns', label: 'Plan deterministic turns', resource_class: 'gpu:llm', status: 'queued' },
          { id: 'run-turns', label: 'Run autoplay turns', resource_class: 'cpu', status: 'queued' },
          { id: 'write-report', label: 'Write autoplay report', resource_class: 'cpu', status: 'queued' },
        ],
      });
    },
    onSuccess: async () => {
      await invalidateRpgWorkspaceQueries();
    },
  });
  const activeHermesSequenceJob = rpgJobs
    .filter((job) => job.type === 'rpg.hermes.sequence.execute')
    .sort((left, right) => timestampMs(right.created_at) - timestampMs(left.created_at))[0];
  const hermesSequenceJobMutation = useMutation({
    mutationFn: (action: 'start' | 'pause' | 'resume' | 'cancel') => {
      if (action === 'cancel' && activeHermesSequenceJob) {
        return omnixApiClient.cancelJob(activeHermesSequenceJob.id, 'Canceled from Hermes sequence job controls.');
      }
      return omnixApiClient.createJob({
        module: 'rpg',
        type: action === 'start' ? 'rpg.hermes.sequence.execute' : 'rpg.hermes.sequence.control',
        resource_class: 'cpu',
        priority: 0,
        input_ref: selectedLiveSessionId ? { session_id: selectedLiveSessionId } : null,
        input_payload: {
          action,
          sequence_id: hermesSequencePreview?.sequence_id,
          current_item_index: 0,
          item_count: hermesSequencePreview?.items?.length ?? 0,
          source: 'rpg-workspace',
        },
        stages: [
          { id: 'load-sequence', label: 'Load Hermes sequence state', resource_class: 'cpu', status: 'queued' },
          { id: 'execute-approved-step', label: 'Execute approved RPG step', resource_class: 'cpu', status: 'queued' },
          { id: 'persist-progress', label: 'Persist sequence progress', resource_class: 'cpu', status: 'queued' },
          { id: 'refresh-ui', label: 'Refresh RPG workspace', resource_class: 'cpu', status: 'queued' },
        ],
      });
    },
    onSuccess: async () => {
      await invalidateRpgWorkspaceQueries();
    },
  });
  const recoveredSubmittedTurnJob = (() => {
    const pending = pendingTurnSubmissionRef.current;
    if (!pending) return undefined;
    const recoveryStart = pending.submittedAt - 5_000;
    const recoveryEnd = pending.submittedAt + RPG_TURN_RECOVERY_WINDOW_MS;
    return rpgJobs
      .filter((job) => {
        const sessionId = typeof job.input_ref?.session_id === 'string' ? job.input_ref.session_id : '';
        const command = typeof job.input_payload?.command === 'string' ? job.input_payload.command.trim() : '';
        const createdAt = timestampMs(job.created_at);
        return isRpgTurnJobType(job.type)
          && sessionId === pending.sessionId
          && command === pending.command
          && (!createdAt || (createdAt >= recoveryStart && createdAt <= recoveryEnd));
      })
      .sort((left, right) => timestampMs(right.created_at) - timestampMs(left.created_at))[0];
  })();
  const submittedTurnJob = createJobMutation.data
    ? submittedTurnJobFromQuery ?? rpgJobs.find((job) => job.id === createJobMutation.data.id) ?? createJobMutation.data
    : recoveredSubmittedTurnJob;
  const submittedTurnFailed = submittedTurnJob
    ? ['failed', 'canceled', 'stale'].includes(submittedTurnJob.status)
    : false;
  const submittedTurnFailureMessage = submittedTurnFailed
    ? `RPG turn ${submittedTurnJob?.status}: ${submittedTurnJob?.error?.message ?? 'The turn did not produce a response.'}`
    : '';
  const recoveredAfterQueueTimeout = Boolean(!createJobMutation.data && createJobMutation.isError && recoveredSubmittedTurnJob);
  const isReconcilingTimedOutTurn = Boolean(createJobMutation.error instanceof ApiTimeoutError && !submittedTurnJob);
  const submitStatus = createJobMutation.isPending
    ? 'queueing'
    : submittedTurnFailed
      ? 'error'
      : submittedTurnJob
        ? submittedTurnJob.status
        : isReconcilingTimedOutTurn
          ? 'reconciling'
        : createJobMutation.isError
          ? 'error'
          : createJobMutation.data?.status ?? 'ready';
  const checkpointControlStatus = createCheckpointMutation.isPending
    ? 'Creating checkpoint…'
    : createCheckpointMutation.isError
      ? 'Checkpoint request failed.'
      : createCheckpointMutation.data?.checkpoint_id
        ? `Checkpoint created: ${createCheckpointMutation.data.checkpoint_id}`
        : undefined;
  const autoplayStatusLabel = autoplayMutation.isPending
    ? 'Updating autoplay…'
    : autoplayMutation.isError
      ? 'Autoplay control failed.'
      : activeAutoplayJob
        ? `${activeAutoplayJob.status} • ${activeAutoplayJob.id}`
        : 'Off';
  const isRefreshingRpgQueries = inventoryQuery.isFetching || jobsQuery.isFetching || assetsQuery.isFetching || reportsQuery.isFetching;
  const selectCommand = (command: string) => setValue('command', command, { shouldDirty: true, shouldValidate: true });
  const applyLoadoutAction = (request: RpgLoadoutActionRequest) => {
    if (!selectedLiveSessionId) {
      selectCommand('Select or create a live RPG session before using inventory or abilities.');
      return;
    }
    loadoutActionMutation.mutate({ sessionId: selectedLiveSessionId, request });
  };
  const submittedTurnStoryMessages = buildSubmittedTurnStoryMessages(
    submittedTurnJob,
    heroSummary.name,
    heroSummary.avatar,
    selectedLiveSessionId,
  );
  const missingSubmittedTurnMessages = submittedTurnStoryMessages.filter(
    (submitted) => !storyMessages.some((message) => (
      (submitted.interactionId && submitted.interactionId === message.interactionId)
      || submitted.text === message.text
    )),
  );
  const visibleStoryMessages = missingSubmittedTurnMessages.length
    ? [...storyMessages, ...missingSubmittedTurnMessages].slice(-40)
    : storyMessages;

  return (
    <WorkspacePanel labelledBy="module-title" className="rpg-workstation">
      <h2 id="module-title" className="workspace-module-heading">{module.label}</h2>
      <header className="rpg-unified-header" aria-label="Campaign menu header">
        <div className="rpg-campaign-menu-host" ref={setCampaignMenuHost} />
        <RpgWorkspaceHeader
          isLiveDataExpanded={isLiveDataExpanded}
          isPlayerRailCollapsed={isPlayerRailCollapsed}
          isWorldRailCollapsed={isWorldRailCollapsed}
          module={module}
          onToggleLiveData={() => setIsLiveDataExpanded((value) => !value)}
          onTogglePlayerRail={() => setIsPlayerRailCollapsed((value) => !value)}
          onToggleWorldRail={() => setIsWorldRailCollapsed((value) => !value)}
          selectedSessionSummary={selectedSessionSummary}
          submitStatus={submitStatus}
        />
      </header>

      <RpgLiveDataStatus
        cards={liveDataStatusCards}
        expanded={isLiveDataExpanded}
        hideWhenCollapsed
        onExpandedChange={setIsLiveDataExpanded}
        showToggle={false}
      />

      <div className={dashboardClassName}>
        {isPlayerRailCollapsed ? null : (
          <RpgPlayerRail
            activeQuests={activeQuests}
            className="rpg-rail-expanded"
            equippedGear={equippedGear}
            heroStats={heroStats}
            heroSummary={heroSummary}
            hermesRouteDecision={hermesRouteDecision}
            hermesSuggestionState={hermesSuggestionState}
            hermesSuggestions={hermesSuggestions}
            hermesTurnReadout={hermesTurnReadout}
            onApprovedFlowAccepted={async (result) => {
              setLatestHermesExecutionResult(result);
              await invalidateRpgWorkspaceQueries();
            }}
            onSelectCommand={selectCommand}
            partyMembers={partyMembers}
            survival={survival}
          />
        )}

        <main className="rpg-center-stage" aria-label="Story scene and actions">
          <RpgStoryScene
            heroSummary={heroSummary}
            recentEvents={recentEvents}
            selectedSessionSummary={selectedSessionSummary}
            storyMessages={visibleStoryMessages}
          >
            <RpgActionComposer
              campaignMenuHost={campaignMenuHost}
              canSaveGame={Boolean(selectedLiveSessionId)}
              commandRegistration={register('command', { required: true })}
              hasCommandError={Boolean(errors.command)}
              isPending={createJobMutation.isPending || isReconcilingTimedOutTurn}
              onQuickAction={selectCommand}
              onSaveGame={async () => {
                const checkpoint = await createCheckpointMutation.mutateAsync();
                return checkpoint.checkpoint_id;
              }}
              onSubmit={handleSubmit((values) => createJobMutation.mutate(values))}
              quickActions={responseOptions}
              renderNewCampaign={(closeLauncher) => (
                <RpgCreateCampaignWizard
                  onCreateCampaign={(request) => createCampaignMutation.mutateAsync(request)}
                  onEnterWorld={closeLauncher}
                />
              )}
              selectedSessionId={selectedSessionId}
              sessionRegistration={register('sessionId')}
              sessionSummaries={sessionSummaries}
            />
            <FeatureValidationMessage show={Boolean(errors.command)} message="Enter a command before queueing an RPG turn." />
            <FeatureValidationMessage show={submittedTurnFailed} message={submittedTurnFailureMessage} />
            <FeatureSubmitFeedback
              error={recoveredAfterQueueTimeout || isReconcilingTimedOutTurn ? null : createJobMutation.error}
              errorPrefix="RPG turn request"
              isError={createJobMutation.isError && !recoveredAfterQueueTimeout && !isReconcilingTimedOutTurn}
              isPending={createJobMutation.isPending || isReconcilingTimedOutTurn}
              jobId={submittedTurnFailed ? undefined : submittedTurnJob?.id}
              pendingMessage={isReconcilingTimedOutTurn ? 'Still checking the RPG job queue for this turn...' : 'Queueing RPG turn job…'}
              successPrefix="RPG turn job queued"
            />
          </RpgStoryScene>

          <RpgHermesSequenceReviewPanel
            assistMode={hermesAssistMode}
            error={hermesSequenceReviewMutation.error}
            isPending={hermesSequenceReviewMutation.isPending}
            onAssistModeChange={setHermesAssistMode}
            onReview={() => hermesSequenceReviewMutation.mutate()}
            onUseFirstItem={selectCommand}
            sequence={hermesSequencePreview}
          />
          <RpgHermesExecutionResult result={latestHermesExecutionResult} />
          <RpgHermesSequenceJobPanel
            activeJob={activeHermesSequenceJob}
            isPending={hermesSequenceJobMutation.isPending}
            onCancel={() => hermesSequenceJobMutation.mutate('cancel')}
            onPause={() => hermesSequenceJobMutation.mutate('pause')}
            onResume={() => hermesSequenceJobMutation.mutate('resume')}
            onStart={() => hermesSequenceJobMutation.mutate('start')}
          />
          <RpgHermesExecutionHistory
            isLoading={hermesExecutionLedgerQuery.isPending}
            items={hermesExecutionLedgerQuery.data?.items ?? []}
          />

          <RpgCombatSurface combat={combatSurface} onSelectCommand={selectCommand} />

          <RpgNarrativeTabs journalDetail={journalDetail} journalEntries={journalEntries} logEntries={narrativeLogEntries} recentEvents={recentEvents} />

          <RpgLoadoutTabs
            hotbarAbilities={hotbarAbilities}
            inventoryItems={inventoryItems}
            isApplyingLoadoutAction={loadoutActionMutation.isPending}
            onApplyLoadoutAction={applyLoadoutAction}
            onSelectCommand={selectCommand}
            selectedSessionId={selectedLiveSessionId}
          />
          <FeatureSubmitFeedback
            error={loadoutActionMutation.error}
            errorPrefix="RPG loadout action"
            isError={loadoutActionMutation.isError}
            isPending={loadoutActionMutation.isPending}
            pendingMessage="Applying deterministic loadout action…"
            successPrefix="RPG loadout action applied"
          />
        </main>

        {isWorldRailCollapsed ? null : (
          <RpgWorldRail
            autoplayRunning={Boolean(activeAutoplayJob)}
            autoplayStatusLabel={autoplayStatusLabel}
            className="rpg-rail-expanded"
            checkpointControlStatus={checkpointControlStatus}
            checkpointSummary={checkpointSummary}
            encounter={encounter}
            isAutoplayPending={autoplayMutation.isPending}
            isCreatingCheckpoint={createCheckpointMutation.isPending}
            isRefreshingJobs={isRefreshingRpgQueries}
            jobCards={jobCards}
            npcRelationships={npcRelationships}
            onCreateCheckpoint={() => createCheckpointMutation.mutate()}
            onRefreshJobs={() => void invalidateRpgWorkspaceQueries()}
            onToggleAutoplay={() => autoplayMutation.mutate()}
            reportsHref="/api/reports"
            rpgAssets={rpgAssets}
            rpgJobCount={rpgJobs.length}
            rpgReportCount={rpgReports.length}
            selectedSessionSummary={selectedSessionSummary}
            worldStateRows={worldStateRows}
          />
        )}
      </div>
    </WorkspacePanel>
  );
}
