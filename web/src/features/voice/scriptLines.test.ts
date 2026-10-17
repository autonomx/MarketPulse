import { describe, expect, it } from 'vitest';
import { parseScriptSegments, parseScriptSpeakers } from './scriptLines';

describe('Voice Studio script parsing', () => {
  it('detects one speaker per tagged character and keeps per-line stages', () => {
    const script = 'dave: hello there\nbob: how do you do\nmarry: i am doing fine\ndave: now lets get to the topic\nmarry: agreed.';

    expect(parseScriptSpeakers(script)).toEqual([
      { name: 'dave', count: 2 },
      { name: 'bob', count: 1 },
      { name: 'marry', count: 2 },
    ]);
    expect(parseScriptSegments(script)).toEqual([
      { index: 0, speaker: 'dave', text: 'hello there' },
      { index: 1, speaker: 'bob', text: 'how do you do' },
      { index: 2, speaker: 'marry', text: 'i am doing fine' },
      { index: 3, speaker: 'dave', text: 'now lets get to the topic' },
      { index: 4, speaker: 'marry', text: 'agreed.' },
    ]);
  });

  it('assumes a single Narrator speaker when no character is specified', () => {
    expect(parseScriptSpeakers('hello there')).toEqual([{ name: 'Narrator', count: 1 }]);
    expect(parseScriptSegments('hello there')).toEqual([{ index: 0, speaker: 'Narrator', text: 'hello there' }]);
  });
});
