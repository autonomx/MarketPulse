import { describe, expect, it } from 'vitest';
import { parseJson } from '../../api/schemas/streams';
import { storyAudioStreamControlMessageSchema } from './storyAudioMessages';

describe('story audio stream messages', () => {
  it('checks story audio control messages by type', () => {
    expect(parseJson(storyAudioStreamControlMessageSchema, '{"type":"start","total_segments":4}')).toEqual({ type: 'start', total_segments: 4 });
    expect(parseJson(storyAudioStreamControlMessageSchema, '{"type":"error","message":null,"error":"busy"}')).toEqual({ type: 'error', message: undefined, error: 'busy' });
    expect(parseJson(storyAudioStreamControlMessageSchema, '{"type":"progress"}')).toBeNull();
  });
});
