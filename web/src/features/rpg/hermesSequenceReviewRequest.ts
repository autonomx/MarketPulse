import type { HermesRpgSequenceRequest } from './api/hermesRpgSequenceClient';
import type { HermesRpgSuggestion } from './api/hermesClient';
import type { RpgQuickActionPreview, RpgSessionSummaryPreview } from './rpgUiState';

interface BuildHermesSequenceReviewRequestInput {
  assistMode?: string;
  quickActions: RpgQuickActionPreview[];
  selectedSessionSummary: RpgSessionSummaryPreview;
  suggestions: HermesRpgSuggestion[];
}

function cleanText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function sequenceRisk(suggestions: HermesRpgSuggestion[]): string {
  return suggestions.some((suggestion) => suggestion.risk === 'high' || suggestion.direct_state_write === true) ? 'high' : 'low';
}

export function buildHermesSequenceReviewRequest({
  quickActions,
  selectedSessionSummary,
  suggestions,
  assistMode = 'review_each_step',
}: BuildHermesSequenceReviewRequestInput): HermesRpgSequenceRequest {
  const suggestionItems = suggestions
    .map((suggestion, index) => {
      const statement = cleanText(suggestion.command);
      if (!statement) return null;
      return {
        item_id: cleanText(suggestion.id) || `suggestion-${index + 1}`,
        statement,
        expected_effect: cleanText(suggestion.reason) || cleanText(suggestion.label) || 'Review prepared RPG command.',
        user_gate: suggestion.requires_user_click === true || suggestion.direct_state_write === true || suggestion.risk === 'high',
      };
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item));

  const fallbackItems = quickActions
    .map((action, index) => {
      const statement = cleanText(action.command);
      if (!statement) return null;
      return {
        item_id: `quick-${index + 1}`,
        statement,
        expected_effect: cleanText(action.label) || 'Review quick RPG command.',
        user_gate: false,
      };
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item));

  const items = (suggestionItems.length ? suggestionItems : fallbackItems).slice(0, 5);
  return {
    session_id: selectedSessionSummary.source === 'live' ? selectedSessionSummary.id : undefined,
    assist_mode: assistMode,
    sequence_id: `ui-${selectedSessionSummary.id || 'preview'}-review`,
    objective: `Review next RPG actions for ${selectedSessionSummary.location || selectedSessionSummary.title || 'the current session'}`,
    domain: 'rpg',
    state_owner: 'rpg_sim',
    risk: suggestionItems.length ? sequenceRisk(suggestions) : 'low',
    status: 'draft',
    items,
  };
}
