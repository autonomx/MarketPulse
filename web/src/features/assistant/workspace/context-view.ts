import type { BudgetedContextAssembly } from './budget';

export type ContextPanelTab = 'context' | 'memory' | 'knowledge' | 'instructions' | 'tools' | 'voice' | 'audit';

export type ContextPanelSummary = {
  tabs: ContextPanelTab[];
  includedSourceCount: number;
  omittedSourceCount: number;
  estimatedTokens: number;
  maxTokens: number;
};

export const CONTEXT_PANEL_TABS: ContextPanelTab[] = [
  'context',
  'memory',
  'knowledge',
  'instructions',
  'tools',
  'voice',
  'audit',
];

export function createContextPanelSummary(context: BudgetedContextAssembly): ContextPanelSummary {
  return {
    tabs: [...CONTEXT_PANEL_TABS],
    includedSourceCount: context.includedSources.length,
    omittedSourceCount: context.omittedSources.length,
    estimatedTokens: context.estimatedTokens,
    maxTokens: context.budget.maxTokens,
  };
}

export function isContextPanelTab(value: string): value is ContextPanelTab {
  return CONTEXT_PANEL_TABS.includes(value as ContextPanelTab);
}
