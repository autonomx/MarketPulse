export type TextSegmentKind = 'draft' | 'complete';

export type TextSegment = {
  id: string;
  text: string;
  kind: TextSegmentKind;
  createdAt: string;
};

export function createTextSegment(segment: TextSegment): TextSegment {
  return { ...segment };
}

export function getCompleteText(segments: TextSegment[]): string {
  return segments
    .filter((segment) => segment.kind === 'complete')
    .map((segment) => segment.text.trim())
    .filter(Boolean)
    .join(' ');
}

export function replaceDraftTextSegment(segments: TextSegment[], next: TextSegment): TextSegment[] {
  return [...segments.filter((segment) => segment.kind !== 'draft'), next];
}
