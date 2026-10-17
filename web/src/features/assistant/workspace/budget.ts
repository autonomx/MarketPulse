import type { ContextAssembly, ContextSource } from './context';

export type ContextBudget = {
  maxTokens: number;
  reserved: {
    system: number;
    conversation: number;
    memory: number;
    knowledge: number;
    tools: number;
    response: number;
  };
};

export type BudgetedContextAssembly = ContextAssembly & {
  budget: ContextBudget;
  includedSources: ContextSource[];
  omittedSources: ContextSource[];
  estimatedTokens: number;
};

export type ContextBudgetManager = {
  maxTokens: number;
  allocate(context: ContextAssembly, budget?: ContextBudget): BudgetedContextAssembly;
};

export function createDefaultContextBudget(maxTokens: number): ContextBudget {
  return {
    maxTokens,
    reserved: {
      system: Math.floor(maxTokens * 0.15),
      conversation: Math.floor(maxTokens * 0.35),
      memory: Math.floor(maxTokens * 0.15),
      knowledge: Math.floor(maxTokens * 0.2),
      tools: Math.floor(maxTokens * 0.05),
      response: Math.floor(maxTokens * 0.1),
    },
  };
}

export function estimateContextSourceTokens(source: ContextSource): number {
  if (typeof source.tokenEstimate === 'number') return source.tokenEstimate;
  return Math.max(1, Math.ceil(`${source.title ?? ''} ${source.reasonIncluded}`.trim().length / 4));
}

export function allocateContextBudget(
  context: ContextAssembly,
  budget: ContextBudget = createDefaultContextBudget(8192),
): BudgetedContextAssembly {
  const includedSources: ContextSource[] = [];
  const omittedSources: ContextSource[] = [];
  let estimatedTokens = 0;

  for (const source of context.sources) {
    const sourceTokens = estimateContextSourceTokens(source);
    if (estimatedTokens + sourceTokens <= budget.maxTokens - budget.reserved.response) {
      includedSources.push(source);
      estimatedTokens += sourceTokens;
    } else {
      omittedSources.push(source);
    }
  }

  return {
    ...context,
    budget,
    includedSources,
    omittedSources,
    estimatedTokens,
  };
}

export function createContextBudgetManager(maxTokens: number): ContextBudgetManager {
  return {
    maxTokens,
    allocate: (context, budget = createDefaultContextBudget(maxTokens)) => allocateContextBudget(context, budget),
  };
}
