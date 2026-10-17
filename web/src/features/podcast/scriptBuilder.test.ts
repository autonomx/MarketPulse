import { describe, expect, it } from 'vitest';
import { buildConversationalPodcastSegments } from './scriptBuilder';

const host = { name: 'Host', role: 'Host', identity: 'Host / Moderator', beliefs: '', personality: '', speakingStyle: '', goal: '', instructions: '' };
const engineer = { name: 'Ada', role: 'Engineer', identity: 'Research engineer', beliefs: '', personality: '', speakingStyle: '', goal: '', instructions: 'to explain the tradeoffs' };

function words(rows: Array<{ text: string }>): number {
  return rows.reduce((total, row) => total + row.text.split(/\s+/).filter(Boolean).length, 0);
}

describe('buildConversationalPodcastSegments', () => {
  it('opens with the brief and closes with a final thought on the title', () => {
    const rows = buildConversationalPodcastSegments('AI markets', 'Will the AI market turn?', 'Founders', [host, engineer], '5');
    expect(rows[0].text).toContain('Welcome. Today we are asking: Will the AI market turn?');
    expect(rows.at(-1)?.text).toMatch(/^Final thought: AI markets will not be settled by slogans/);
    expect(rows.map((row) => row.index)).toEqual(rows.map((_, index) => index));
  });

  it('alternates speakers and gives each the lines of their lens', () => {
    const rows = buildConversationalPodcastSegments('Topic', 'Brief', 'Teams', [host, engineer], '3');
    expect(rows.slice(0, 4).map((row) => row.speaker)).toEqual(['Host', 'Ada', 'Host', 'Ada']);
    // The engineer speaks with the technical lens.
    expect(rows[1].text).toContain('A concrete example is developer tooling.');
    // A speaker's instructions become their angle in their opening line.
    const solo = buildConversationalPodcastSegments('Topic', 'Brief', 'Teams', [engineer], '3');
    expect(solo[0].text).toContain('measurable savings. My angle is explain the tradeoffs.');
  });

  it('writes about 150 words a minute, and longer episodes get more segments', () => {
    const short = buildConversationalPodcastSegments('Topic', 'Brief', 'Teams', [host, engineer], '2');
    const long = buildConversationalPodcastSegments('Topic', 'Brief', 'Teams', [host, engineer], '6');
    expect(words(short)).toBeGreaterThanOrEqual(300);
    expect(words(long)).toBeGreaterThanOrEqual(900);
    expect(long.length).toBeGreaterThan(short.length);
  });

  it('caps a script at 96 segments plus the final thought', () => {
    const rows = buildConversationalPodcastSegments('Topic', 'Brief', 'Teams', [host, engineer], '120');
    expect(rows).toHaveLength(97);
  });

  it('falls back to a host, a placeholder title and a default brief', () => {
    const rows = buildConversationalPodcastSegments('', '', 'Listeners', [], 'not a number');
    expect(new Set(rows.map((row) => row.speaker))).toEqual(new Set(['Host']));
    expect(rows[0].text).toContain('Discuss the topic with practical examples');
    expect(rows.at(-1)?.text).toContain('Untitled episode');
  });
});
