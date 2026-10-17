export type SpeakerIdentity =
  | 'Host / Moderator'
  | 'AI Researcher'
  | 'AI Safety Professor'
  | 'Economist'
  | 'Game Designer'
  | 'Professor'
  | 'Journalist'
  | 'Founder'
  | 'Policy Expert';

export type SpeakerBelief =
  | 'Neutral'
  | 'Optimistic'
  | 'Pessimistic'
  | 'Skeptical'
  | 'Tech-first'
  | 'Human-first'
  | 'Traditional'
  | 'Regulation-focused'
  | 'Open-source';

export type SpeakerPersonality =
  | 'Warm'
  | 'Direct'
  | 'Calm'
  | 'Curious'
  | 'Analytical'
  | 'Firm'
  | 'Energetic'
  | 'Sarcastic'
  | 'Patient'
  | 'Playful';

export type SpeakingStyle =
  | 'Conversational'
  | 'Academic'
  | 'Formal'
  | 'Punchy'
  | 'Storytelling'
  | 'Concise'
  | 'Fast-paced'
  | 'Dramatic'
  | 'Casual';

export type SpeakerGoal =
  | 'Moderate'
  | 'Educate'
  | 'Defend'
  | 'Challenge'
  | 'Summarize'
  | 'Persuade'
  | 'Question'
  | 'Debunk'
  | 'Entertain';

export interface SegmentSpeakerGoal {
  segmentId: string;
  goal: SpeakerGoal;
}

export type SpeakerRelationshipKind = 'moderates' | 'respects' | 'disagrees_with' | 'challenges' | 'amplifies' | 'interrupts';

export interface SpeakerRelationship {
  fromSpeakerId: string;
  toSpeakerId: string;
  relationship: SpeakerRelationshipKind;
  intensity: number;
  behavior: string;
}

export interface VoiceMapping {
  speakerId: string;
  voiceId: string;
  voiceDisplayName: string;
  previewAvailable: boolean;
  fallbackVoiceId?: string;
}

export interface SpeakerProfile {
  id: string;
  name: string;
  role: string;
  avatar: string;
  identity: SpeakerIdentity;
  beliefs: SpeakerBelief[];
  personality: SpeakerPersonality[];
  speakingStyle: SpeakingStyle[];
  defaultGoal: SpeakerGoal;
  segmentGoals: SegmentSpeakerGoal[];
  voiceMapping: VoiceMapping;
}

export const speakerIdentityPresets: SpeakerIdentity[] = [
  'Host / Moderator',
  'AI Researcher',
  'AI Safety Professor',
  'Economist',
  'Game Designer',
  'Professor',
  'Journalist',
  'Founder',
  'Policy Expert',
];

export const speakerBeliefPresets: SpeakerBelief[] = [
  'Neutral',
  'Optimistic',
  'Pessimistic',
  'Skeptical',
  'Tech-first',
  'Human-first',
  'Traditional',
  'Regulation-focused',
  'Open-source',
];

export const speakerPersonalityPresets: SpeakerPersonality[] = [
  'Warm',
  'Direct',
  'Calm',
  'Curious',
  'Analytical',
  'Firm',
  'Energetic',
  'Sarcastic',
  'Patient',
  'Playful',
];

export const speakingStylePresets: SpeakingStyle[] = [
  'Conversational',
  'Academic',
  'Formal',
  'Punchy',
  'Storytelling',
  'Concise',
  'Fast-paced',
  'Dramatic',
  'Casual',
];

export const speakerGoalPresets: SpeakerGoal[] = [
  'Moderate',
  'Educate',
  'Defend',
  'Challenge',
  'Summarize',
  'Persuade',
  'Question',
  'Debunk',
  'Entertain',
];

export const relationshipPresets: SpeakerRelationshipKind[] = [
  'moderates',
  'respects',
  'disagrees_with',
  'challenges',
  'amplifies',
  'interrupts',
];

export const mockPodcastSpeakerProfiles: SpeakerProfile[] = [
  {
    id: 'host',
    name: 'Host / You',
    role: 'Host',
    avatar: 'H',
    identity: 'Host / Moderator',
    beliefs: ['Neutral'],
    personality: ['Warm', 'Direct'],
    speakingStyle: ['Conversational'],
    defaultGoal: 'Moderate',
    segmentGoals: [{ segmentId: 'opening', goal: 'Moderate' }],
    voiceMapping: {
      speakerId: 'host',
      voiceId: 'host_confident_calm',
      voiceDisplayName: 'Host – Confident Calm',
      previewAvailable: true,
    },
  },
  {
    id: 'guest_a',
    name: 'Guest A',
    role: 'AI Researcher',
    avatar: 'GA',
    identity: 'AI Researcher',
    beliefs: ['Optimistic', 'Tech-first'],
    personality: ['Calm', 'Curious'],
    speakingStyle: ['Academic', 'Conversational'],
    defaultGoal: 'Educate',
    segmentGoals: [
      { segmentId: 'opening', goal: 'Educate' },
      { segmentId: 'debate', goal: 'Defend' },
    ],
    voiceMapping: {
      speakerId: 'guest_a',
      voiceId: 'dr_alex_morgan',
      voiceDisplayName: 'Dr. Alex Morgan',
      previewAvailable: true,
    },
  },
  {
    id: 'guest_b',
    name: 'Guest B',
    role: 'AI Safety Professor',
    avatar: 'GB',
    identity: 'AI Safety Professor',
    beliefs: ['Skeptical', 'Human-first'],
    personality: ['Analytical', 'Firm'],
    speakingStyle: ['Formal', 'Punchy'],
    defaultGoal: 'Challenge',
    segmentGoals: [
      { segmentId: 'debate', goal: 'Challenge' },
      { segmentId: 'closing', goal: 'Summarize' },
    ],
    voiceMapping: {
      speakerId: 'guest_b',
      voiceId: 'jordan_lee',
      voiceDisplayName: 'Jordan Lee',
      previewAvailable: true,
    },
  },
];

export const mockPodcastRelationships: SpeakerRelationship[] = [
  {
    fromSpeakerId: 'host',
    toSpeakerId: 'guest_a',
    relationship: 'moderates',
    intensity: 0.7,
    behavior: 'keeps the optimistic technical explanation grounded for the audience',
  },
  {
    fromSpeakerId: 'guest_b',
    toSpeakerId: 'guest_a',
    relationship: 'disagrees_with',
    intensity: 0.8,
    behavior: 'pushes back when claims sound too optimistic or under-evidenced',
  },
  {
    fromSpeakerId: 'guest_a',
    toSpeakerId: 'guest_b',
    relationship: 'respects',
    intensity: 0.6,
    behavior: 'acknowledges risks before defending practical benefits',
  },
];
