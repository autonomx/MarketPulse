import type { LiveTaskContract } from './live-task-contract';

export type LiveAcousticClass = 'speech' | 'assistant_echo' | 'noise';
export type LiveInputRole = 'ongoing_material' | 'conversation' | 'assistant_control' | 'uncertain';
export type LiveCoordinationAction =
  | 'append'
  | 'append_and_observe'
  | 'respond'
  | 'interrupt_and_respond'
  | 'stop_output'
  | 'ignore';

export type LiveInputCoordination = {
  acousticClass: LiveAcousticClass;
  inputRole: LiveInputRole;
  action: LiveCoordinationAction;
  confidence: number;
  reason: string;
};

export type LiveInputCoordinationContext = {
  taskContract: LiveTaskContract;
  assistantSpeaking: boolean;
  acousticClass?: LiveAcousticClass;
  echoConfidence?: number;
};

const BACKCHANNEL_PATTERN = /^(?:m+h+m+|u+h[- ]?huh|yeah|yep|right|okay|ok|got it|sure)[.!? ]*$/i;
const WAKE_STOP_PATTERN = /^(?:(?:hey[ ,]+)?maya[ ,]+)?(?:please[ ,]+)?(?:stop|pause|be quiet|hold on|cancel)(?:[.!? ]*)$/i;
const INTERRUPT_QUESTION_PATTERN = /^(?:wait|hold on|hang on|why|what do you mean|can you explain|but why)\b/i;
const DIRECT_ASSISTANT_PATTERN = /^(?:(?:hey[ ,]+)?maya[ ,]+|omnix[ ,]+|assistant[ ,]+)/i;

export function classifyLiveInput(
  text: string,
  context: LiveInputCoordinationContext,
): LiveInputCoordination {
  const normalized = text.replace(/\s+/g, ' ').trim();
  const acousticClass = context.acousticClass ?? 'speech';
  if (acousticClass === 'assistant_echo' || (context.echoConfidence ?? 0) >= 0.9) {
    return decision(acousticClass, 'uncertain', 'ignore', 0.99, 'assistant_echo');
  }
  if (acousticClass === 'noise' || !normalized) {
    return decision(acousticClass, 'uncertain', 'ignore', 0.99, 'noise_or_empty');
  }
  if (WAKE_STOP_PATTERN.test(normalized) && (DIRECT_ASSISTANT_PATTERN.test(normalized) || context.taskContract.inputRole !== 'ongoing_material')) {
    return decision(acousticClass, 'assistant_control', 'stop_output', 0.98, 'unambiguous_stop_control');
  }
  if (BACKCHANNEL_PATTERN.test(normalized) && context.assistantSpeaking) {
    return decision(acousticClass, 'conversation', 'ignore', 0.92, 'listener_backchannel');
  }
  if (context.assistantSpeaking && INTERRUPT_QUESTION_PATTERN.test(normalized)) {
    return decision(acousticClass, 'conversation', 'interrupt_and_respond', 0.9, 'interrupting_question');
  }
  if (DIRECT_ASSISTANT_PATTERN.test(normalized)) {
    return decision(
      acousticClass,
      'conversation',
      context.assistantSpeaking ? 'interrupt_and_respond' : 'respond',
      0.9,
      'direct_assistant_address',
    );
  }
  if (context.taskContract.inputRole === 'ongoing_material') {
    const action = context.taskContract.assistantInitiative === 'interjecting'
      ? 'append_and_observe'
      : 'append';
    return decision(acousticClass, 'ongoing_material', action, 0.94, 'task_contract_material');
  }
  if (context.taskContract.inputRole === 'mixed') {
    if (/\?$/.test(normalized) || /^(?:can|could|would|will|do|does|did|is|are|why|what|how|when|where|who)\b/i.test(normalized)) {
      return decision(
        acousticClass,
        'conversation',
        context.assistantSpeaking ? 'interrupt_and_respond' : 'respond',
        0.82,
        'mixed_task_question',
      );
    }
    return decision(acousticClass, 'ongoing_material', 'append_and_observe', 0.72, 'mixed_task_material_default');
  }
  return decision(
    acousticClass,
    'conversation',
    context.assistantSpeaking ? 'interrupt_and_respond' : 'respond',
    0.88,
    'conversation_task_default',
  );
}

function decision(
  acousticClass: LiveAcousticClass,
  inputRole: LiveInputRole,
  action: LiveCoordinationAction,
  confidence: number,
  reason: string,
): LiveInputCoordination {
  return { acousticClass, inputRole, action, confidence, reason };
}
