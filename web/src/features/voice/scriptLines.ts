export interface ScriptSpeakerRow {
  name: string;
  count: number;
}

export interface ScriptSegmentRow {
  index: number;
  speaker: string;
  text: string;
}

const DEFAULT_UNTAGGED_SPEAKER = 'Narrator';

export function parseScriptSpeakers(text: string): ScriptSpeakerRow[] {
  const counts = new Map<string, number>();
  for (const segment of parseScriptSegments(text)) {
    counts.set(segment.speaker, (counts.get(segment.speaker) ?? 0) + 1);
  }
  return Array.from(counts.entries()).map(([name, count]) => ({ name, count }));
}

export function parseScriptSegments(text: string): ScriptSegmentRow[] {
  const segments: ScriptSegmentRow[] = [];
  let untaggedText: string[] = [];

  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    const tagged = parseTaggedLine(line);
    if (tagged) {
      flushUntagged(segments, untaggedText);
      untaggedText = [];
      segments.push({ index: segments.length, speaker: tagged.speaker, text: tagged.text });
    } else {
      untaggedText.push(line);
    }
  }

  flushUntagged(segments, untaggedText);
  return segments;
}

function parseTaggedLine(line: string): { speaker: string; text: string } | null {
  const colon = line.indexOf(':');
  if (colon <= 0) {
    return null;
  }
  const speaker = line.slice(0, colon).trim();
  const text = line.slice(colon + 1).trim();
  if (!speaker || !text || speaker.length > 50) {
    return null;
  }
  return { speaker, text };
}

function flushUntagged(segments: ScriptSegmentRow[], lines: string[]): void {
  const text = lines.join(' ').trim();
  if (!text) {
    return;
  }
  segments.push({ index: segments.length, speaker: DEFAULT_UNTAGGED_SPEAKER, text });
}
