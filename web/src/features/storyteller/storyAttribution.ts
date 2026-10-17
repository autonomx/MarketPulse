import type { StoryCharacter } from './storyCast';
import type { StoryBlock, StoryDocument } from './storyDocument';

export interface DialogueAttributionIssue {
  blockId: string;
  chapterId: string;
  severity: 'info' | 'warning';
  message: string;
  speakerId: string;
  confidence: number;
}

export interface DialogueAttributionSummary {
  dialogueBlocks: number;
  highConfidence: number;
  narratorFallbacks: number;
  warnings: DialogueAttributionIssue[];
}

const HIGH_CONFIDENCE_THRESHOLD = 0.72;
const speechVerbs = ['said', 'asked', 'whispered', 'announced', 'replied', 'intoned', 'murmured', 'shouted'];

export function validateDialogueAttribution(document: StoryDocument): DialogueAttributionSummary {
  const castIds = new Set(document.cast.map((character) => character.id));
  const issues: DialogueAttributionIssue[] = [];
  let dialogueBlocks = 0;
  let highConfidence = 0;
  let narratorFallbacks = 0;

  for (const chapter of document.chapters) {
    for (const block of chapter.blocks) {
      if (block.kind !== 'dialogue') continue;
      dialogueBlocks += 1;
      if (block.confidence >= HIGH_CONFIDENCE_THRESHOLD && block.speakerId !== 'narrator') highConfidence += 1;
      if (block.speakerId === 'narrator') narratorFallbacks += 1;
      if (!castIds.has(block.speakerId)) {
        issues.push(issueForBlock(block, 'warning', 'Speaker id is not present in the cast registry.'));
      } else if (block.speakerId !== 'narrator' && block.confidence < HIGH_CONFIDENCE_THRESHOLD) {
        issues.push(issueForBlock(block, 'warning', 'Speaker attribution is below the safe confidence threshold.'));
      } else if (block.speakerId === 'narrator') {
        issues.push(issueForBlock(block, 'info', 'Dialogue uses narrator fallback until attribution is explicit.'));
      }
    }
  }

  return { dialogueBlocks, highConfidence, narratorFallbacks, warnings: issues };
}

export function resolveDialogueSpeaker({ context, cast }: { context: string; cast: StoryCharacter[] }): { speaker: StoryCharacter; confidence: number; evidence: string } | null {
  const normalizedContext = context.toLowerCase();
  const candidates = cast
    .filter((character) => character.id !== 'narrator')
    .map((character) => {
      const matchedAlias = character.aliases.find((alias) => normalizedContext.includes(alias.toLowerCase()));
      return matchedAlias ? { speaker: character, confidence: explicitAttributionConfidence(context, matchedAlias), evidence: `nearby alias: ${matchedAlias}` } : null;
    })
    .filter((candidate): candidate is { speaker: StoryCharacter; confidence: number; evidence: string } => Boolean(candidate))
    .sort((left, right) => right.confidence - left.confidence);
  const best = candidates[0];
  return best && best.confidence >= HIGH_CONFIDENCE_THRESHOLD ? best : null;
}

function explicitAttributionConfidence(context: string, alias: string): number {
  const lowerContext = context.toLowerCase();
  const lowerAlias = alias.toLowerCase();
  const aliasIndex = lowerContext.indexOf(lowerAlias);
  if (aliasIndex < 0) return 0;
  const before = lowerContext.slice(Math.max(0, aliasIndex - 40), aliasIndex);
  const after = lowerContext.slice(aliasIndex + lowerAlias.length, aliasIndex + lowerAlias.length + 40);
  if (speechVerbs.some((verb) => before.includes(verb) || after.includes(verb))) return 0.92;
  return 0.74;
}

function issueForBlock(block: Extract<StoryBlock, { kind: 'dialogue' }>, severity: DialogueAttributionIssue['severity'], message: string): DialogueAttributionIssue {
  return { blockId: block.id, chapterId: block.chapterId, severity, message, speakerId: block.speakerId, confidence: block.confidence };
}
