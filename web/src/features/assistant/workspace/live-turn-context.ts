export type AssistantTurnCompletionContext = {
  turnId: string | null;
  questionCount: number;
  createsObligation: boolean;
  updatedAtMs: number;
};

const CONTEXT_TTL_MS = 60_000;
let latestContext: AssistantTurnCompletionContext | null = null;

export function noteAssistantTurnCompletionContext(
  context: Omit<AssistantTurnCompletionContext, 'updatedAtMs'>,
): void {
  latestContext = {
    ...context,
    updatedAtMs: nowMs(),
  };
}

export function readAssistantTurnCompletionContext(
  maxAgeMs = CONTEXT_TTL_MS,
): AssistantTurnCompletionContext | null {
  const current = latestContext;
  if (!current) return null;
  if (Math.max(0, nowMs() - current.updatedAtMs) > Math.max(0, maxAgeMs)) {
    latestContext = null;
    return null;
  }
  return { ...current };
}

export function resetAssistantTurnCompletionContext(): void {
  latestContext = null;
}

function nowMs(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}
