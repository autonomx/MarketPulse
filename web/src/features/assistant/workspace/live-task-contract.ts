export type LiveTaskInputRole = 'conversation' | 'ongoing_material' | 'mixed';
export type LiveAssistantInitiative = 'reactive' | 'interjecting' | 'observing';
export type LiveMinorFeedbackPolicy = 'defer' | 'consolidate' | 'interject';
export type LiveCriticalFeedbackPolicy = 'interject' | 'queue';

export type LiveTaskContract = {
  taskContractId: string;
  version: number;
  instruction: string;
  inputRole: LiveTaskInputRole;
  assistantInitiative: LiveAssistantInitiative;
  defaultRetention: 'ephemeral_session';
  observationPolicy: {
    preferredMaximumSpeechMs: number;
    minorFeedback: LiveMinorFeedbackPolicy;
    criticalFeedback: LiveCriticalFeedbackPolicy;
  };
};

export const DEFAULT_LIVE_TASK_CONTRACT: LiveTaskContract = {
  taskContractId: 'conversation',
  version: 1,
  instruction: 'Have a normal live conversation.',
  inputRole: 'conversation',
  assistantInitiative: 'reactive',
  defaultRetention: 'ephemeral_session',
  observationPolicy: {
    preferredMaximumSpeechMs: 2_500,
    minorFeedback: 'defer',
    criticalFeedback: 'queue',
  },
};

export type LiveTaskTransitionDecision = {
  preserveCapturedMaterial: true;
  preserveFinalizingSegments: true;
  invalidatePendingObservations: boolean;
  invalidateActiveGeneration: boolean;
  cancelReadyOutput: boolean;
  cancelSpeakingOutput: boolean;
};

export function normalizeLiveTaskContract(contract: LiveTaskContract): LiveTaskContract {
  const taskContractId = contract.taskContractId.trim();
  const instruction = contract.instruction.trim();
  if (!taskContractId) throw new Error('Live task contract ID is required.');
  if (!instruction) throw new Error('Live task instruction is required.');
  if (!Number.isInteger(contract.version) || contract.version < 1) {
    throw new Error('Live task contract version must be a positive integer.');
  }
  return {
    ...contract,
    taskContractId,
    instruction,
    observationPolicy: {
      ...contract.observationPolicy,
      preferredMaximumSpeechMs: Math.max(250, Math.min(15_000, Math.round(contract.observationPolicy.preferredMaximumSpeechMs))),
    },
  };
}

export function decideLiveTaskTransition(
  previous: LiveTaskContract,
  next: LiveTaskContract,
): LiveTaskTransitionDecision {
  const changed = previous.taskContractId !== next.taskContractId || previous.version !== next.version;
  const observationPolicyChanged = changed
    || previous.assistantInitiative !== next.assistantInitiative
    || previous.inputRole !== next.inputRole
    || previous.observationPolicy.minorFeedback !== next.observationPolicy.minorFeedback
    || previous.observationPolicy.criticalFeedback !== next.observationPolicy.criticalFeedback;
  const becomesListeningOnly = next.assistantInitiative === 'observing'
    && next.observationPolicy.minorFeedback === 'defer'
    && next.observationPolicy.criticalFeedback === 'queue';
  return {
    preserveCapturedMaterial: true,
    preserveFinalizingSegments: true,
    invalidatePendingObservations: observationPolicyChanged,
    invalidateActiveGeneration: observationPolicyChanged,
    cancelReadyOutput: observationPolicyChanged,
    cancelSpeakingOutput: becomesListeningOnly,
  };
}

export function inferLiveTaskContract(instruction: string, version = 1): LiveTaskContract {
  const normalized = instruction.trim();
  const lower = normalized.toLocaleLowerCase();
  const translation = /\b(?:translate|translation|interpret)\b/.test(lower);
  const editing = /\b(?:correct|grammar|edit|proofread|rewrite)\b/.test(lower);
  const coaching = /\b(?:coach|pronunciation|interrupt when|point out)\b/.test(lower);
  const material = translation || editing || /\b(?:listen to|as i read|while i read|video|audio)\b/.test(lower);
  return normalizeLiveTaskContract({
    taskContractId: translation ? 'translation' : editing ? 'editing' : coaching ? 'coaching' : 'conversation',
    version,
    instruction: normalized || DEFAULT_LIVE_TASK_CONTRACT.instruction,
    inputRole: material ? 'ongoing_material' : coaching ? 'mixed' : 'conversation',
    assistantInitiative: translation || editing || coaching ? 'interjecting' : 'reactive',
    defaultRetention: 'ephemeral_session',
    observationPolicy: {
      preferredMaximumSpeechMs: translation ? 4_000 : 2_500,
      minorFeedback: editing ? 'consolidate' : coaching ? 'interject' : 'defer',
      criticalFeedback: translation || coaching ? 'interject' : 'queue',
    },
  });
}
