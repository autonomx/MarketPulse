import type {
  LiveConversationProfile,
  LiveConversationProfilePatch,
} from './liveConversationProfileClient';

export type LiveConversationModeProfileId = 'balanced' | 'full_duplex' | 'turn_based' | 'listener';

export type LiveConversationModeProfile = {
  id: LiveConversationModeProfileId;
  label: string;
  description: string;
  settings: Readonly<LiveConversationProfilePatch>;
};

export const LIVE_CONVERSATION_MODE_PROFILES: readonly LiveConversationModeProfile[] = [
  {
    id: 'balanced',
    label: 'Balanced',
    description: 'Adaptive timing with safe automatic duplex fallback.',
    settings: {
      presence_preset: 'natural',
      talkativeness: 50,
      conversation_stance: 'automatic',
      conversation_pace: 'balanced',
      interruption_preference: 'balanced',
      assistant_backchannel_mode: 'off',
      initiative_mode: 'gentle',
      idle_threshold_ms: 15_000,
      long_pause_behavior: 'wait',
      response_length: 'conversational',
      response_onset_style: 'adaptive',
      emotional_attunement: 'subtle',
      topic_continuity: 'natural',
      max_idle_prompts: 1,
      duplex_mode: 'automatic',
      pronunciation_save_policy: 'ask',
    },
  },
  {
    id: 'full_duplex',
    label: 'Full duplex',
    description: 'Echo-aware overlap, easy interruption, and natural listener cues.',
    settings: {
      presence_preset: 'engaged',
      talkativeness: 60,
      conversation_stance: 'discuss',
      conversation_pace: 'quick',
      interruption_preference: 'easy',
      assistant_backchannel_mode: 'natural',
      initiative_mode: 'gentle',
      idle_threshold_ms: 15_000,
      long_pause_behavior: 'reassure',
      response_length: 'conversational',
      response_onset_style: 'immediate',
      emotional_attunement: 'expressive',
      topic_continuity: 'natural',
      max_idle_prompts: 1,
      duplex_mode: 'echo_aware',
      pronunciation_save_policy: 'ask',
    },
  },
  {
    id: 'turn_based',
    label: 'Turn-based',
    description: 'Strict half-duplex turns with no prompts or backchannels.',
    settings: {
      presence_preset: 'natural',
      talkativeness: 45,
      conversation_stance: 'automatic',
      conversation_pace: 'balanced',
      interruption_preference: 'finish_more',
      assistant_backchannel_mode: 'off',
      initiative_mode: 'off',
      idle_threshold_ms: 30_000,
      long_pause_behavior: 'wait',
      response_length: 'conversational',
      response_onset_style: 'natural',
      emotional_attunement: 'subtle',
      topic_continuity: 'focused',
      max_idle_prompts: 0,
      duplex_mode: 'half_duplex',
      pronunciation_save_policy: 'ask',
    },
  },
  {
    id: 'listener',
    label: 'Listener',
    description: 'Brief reflective replies, acknowledgements, and low initiative.',
    settings: {
      presence_preset: 'listener',
      talkativeness: 25,
      conversation_stance: 'listen',
      conversation_pace: 'reflective',
      interruption_preference: 'easy',
      assistant_backchannel_mode: 'natural',
      initiative_mode: 'off',
      idle_threshold_ms: 30_000,
      long_pause_behavior: 'wait',
      response_length: 'brief',
      response_onset_style: 'adaptive',
      emotional_attunement: 'subtle',
      topic_continuity: 'focused',
      max_idle_prompts: 0,
      duplex_mode: 'automatic',
      pronunciation_save_policy: 'ask',
    },
  },
] as const;

export function matchLiveConversationModeProfile(
  profile: LiveConversationProfile,
): LiveConversationModeProfileId | null {
  return LIVE_CONVERSATION_MODE_PROFILES.find(({ settings }) => (
    Object.entries(settings).every(([key, value]) => (
      profile[key as keyof LiveConversationProfilePatch] === value
    ))
  ))?.id ?? null;
}
