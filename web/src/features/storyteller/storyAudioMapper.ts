import { buildStoryDocumentFromText, fingerprintText, type StoryDocument } from './storyDocument';
import { loadStoryVoiceCastAny, voiceAssignmentFor } from './storyVoiceCast';

export interface StoryAudioScriptSegment {
  index: number;
  speaker: string;
  text: string;
  voice_id: string | null;
  character_id: string;
  block_id: string;
  chapter_id: string;
}

export interface StoryAudioMapResult {
  document: StoryDocument;
  segments: StoryAudioScriptSegment[];
}

export function mapStoryToAudioSegments(title: string, text: string, fallbackVoiceId: string): StoryAudioMapResult {
  const document = buildStoryDocumentFromText({ title, text });
  const voiceCast = loadStoryVoiceCastAny([document.id, fingerprintText(text)]);
  const narratorVoice = voiceAssignmentFor(voiceCast, 'narrator')?.voiceId || fallbackVoiceId || '';
  const segments: StoryAudioScriptSegment[] = [];

  for (const chapter of document.chapters) {
    for (const block of chapter.blocks) {
      const voice = voiceAssignmentFor(voiceCast, block.speakerId)?.voiceId || narratorVoice || null;
      segments.push({
        index: segments.length,
        speaker: block.kind === 'dialogue' ? block.speakerName : 'Narrator',
        text: block.text,
        voice_id: voice,
        character_id: block.speakerId,
        block_id: block.id,
        chapter_id: chapter.id,
      });
    }
  }

  return { document, segments };
}

export function speakerRowsFromSegments(segments: StoryAudioScriptSegment[]) {
  const counts = new Map<string, number>();
  for (const segment of segments) counts.set(segment.speaker, (counts.get(segment.speaker) ?? 0) + 1);
  return [...counts.entries()].map(([name, count]) => ({ name, count }));
}

export function assignmentRowsFromSegments(segments: StoryAudioScriptSegment[]) {
  const rows = new Map<string, { speaker: string; voice_id: string | null; style: string; line_count: number; character_id: string }>();
  for (const segment of segments) {
    const current = rows.get(segment.character_id);
    rows.set(segment.character_id, { speaker: segment.speaker, voice_id: segment.voice_id, style: segment.character_id === 'narrator' ? 'Story narrator' : 'Character dialogue', line_count: (current?.line_count ?? 0) + 1, character_id: segment.character_id });
  }
  return [...rows.values()];
}
