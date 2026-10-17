import { describe, expect, it } from 'vitest';
import {
  mockPodcastRelationships,
  mockPodcastSpeakerProfiles,
  relationshipPresets,
  speakerGoalPresets,
  speakerIdentityPresets,
  speakingStylePresets,
} from './speakers';

describe('podcast speaker profiles', () => {
  it('gives every speaker a unique id and a voice mapped to that speaker', () => {
    const ids = mockPodcastSpeakerProfiles.map((speaker) => speaker.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const speaker of mockPodcastSpeakerProfiles) {
      expect(speaker.voiceMapping.speakerId).toBe(speaker.id);
      expect(speaker.voiceMapping.voiceId).not.toBe('');
    }
  });

  it('relates only speakers that exist, with known kinds and intensities between 0 and 1', () => {
    const ids = new Set(mockPodcastSpeakerProfiles.map((speaker) => speaker.id));
    for (const relationship of mockPodcastRelationships) {
      expect(ids.has(relationship.fromSpeakerId)).toBe(true);
      expect(ids.has(relationship.toSpeakerId)).toBe(true);
      expect(relationship.fromSpeakerId).not.toBe(relationship.toSpeakerId);
      expect(relationshipPresets).toContain(relationship.relationship);
      expect(relationship.intensity).toBeGreaterThanOrEqual(0);
      expect(relationship.intensity).toBeLessThanOrEqual(1);
    }
  });

  it('draws speaker goals, identities and styles from the presets', () => {
    for (const speaker of mockPodcastSpeakerProfiles) {
      expect(speakerGoalPresets).toContain(speaker.defaultGoal);
      for (const goal of speaker.segmentGoals) expect(speakerGoalPresets).toContain(goal.goal);
      expect(speakerIdentityPresets).toContain(speaker.identity);
      for (const style of speaker.speakingStyle) expect(speakingStylePresets).toContain(style);
    }
  });
});
