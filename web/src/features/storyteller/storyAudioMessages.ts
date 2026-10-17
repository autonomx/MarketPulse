import { z } from 'zod';
import { absentWhenNull as optional } from '../../api/schemas/streams';
import type { StoryAudioStreamControlMessage } from './StoryAudioPanel';

/**
 * A JSON control message on the story audio WebSocket (/ws/audiobook), checked
 * where it enters the app (WP-9.3). The type is the contract; the annotation
 * makes the compiler report drift.
 */
export const storyAudioStreamControlMessageSchema: z.ZodType<StoryAudioStreamControlMessage> = z.discriminatedUnion('type', [
  z.looseObject({ type: z.literal('start'), total_segments: optional(z.number()) }),
  z.looseObject({ type: z.literal('segment'), index: optional(z.number()), speaker: optional(z.string()), text: optional(z.string()) }),
  z.looseObject({ type: z.literal('done'), job_id: optional(z.string()) }),
  z.looseObject({ type: z.literal('stopped') }),
  z.looseObject({ type: z.literal('error'), message: optional(z.string()), error: optional(z.string()) }),
]);
