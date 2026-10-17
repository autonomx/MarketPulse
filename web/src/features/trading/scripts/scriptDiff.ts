/** A line diff of two script versions (TVP-11.3), for the editor's history. */

export type DiffLine = { kind: 'same' | 'added' | 'removed'; text: string; before: number | null; after: number | null };

/** Lines compared cell by cell at most; longer scripts compare their changed middle only by position. */
const MAX_CELLS = 4_000_000;

/** The lines of `before` and `after` as kept, removed and added lines (a longest common subsequence). */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n');
  const b = after.split('\n');
  // Common head and tail first: an edit usually touches a few lines in the middle.
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail += 1;
  const middleA = a.slice(head, a.length - tail);
  const middleB = b.slice(head, b.length - tail);
  const lines: DiffLine[] = [];
  for (let index = 0; index < head; index += 1) lines.push({ kind: 'same', text: a[index], before: index + 1, after: index + 1 });
  const middle = middleA.length * middleB.length <= MAX_CELLS ? lcsDiff(middleA, middleB) : positional(middleA, middleB);
  for (const line of middle) {
    lines.push({
      ...line,
      before: line.before === null ? null : line.before + head,
      after: line.after === null ? null : line.after + head,
    });
  }
  for (let index = tail; index > 0; index -= 1) {
    lines.push({ kind: 'same', text: a[a.length - index], before: a.length - index + 1, after: b.length - index + 1 });
  }
  return lines;
}

function lcsDiff(a: readonly string[], b: readonly string[]): DiffLine[] {
  const width = b.length + 1;
  // lengths[i * width + j]: the longest common subsequence of a[i:] and b[j:].
  const lengths = new Uint32Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lengths[i * width + j] = a[i] === b[j] ? lengths[(i + 1) * width + j + 1] + 1 : Math.max(lengths[(i + 1) * width + j], lengths[i * width + j + 1]);
    }
  }
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      lines.push({ kind: 'same', text: a[i], before: i + 1, after: j + 1 });
      i += 1;
      j += 1;
    } else if (j < b.length && (i === a.length || lengths[i * width + j + 1] >= lengths[(i + 1) * width + j])) {
      lines.push({ kind: 'added', text: b[j], before: null, after: j + 1 });
      j += 1;
    } else {
      lines.push({ kind: 'removed', text: a[i], before: i + 1, after: null });
      i += 1;
    }
  }
  return lines;
}

function positional(a: readonly string[], b: readonly string[]): DiffLine[] {
  return [
    ...a.map((text, index) => ({ kind: 'removed' as const, text, before: index + 1, after: null })),
    ...b.map((text, index) => ({ kind: 'added' as const, text, before: null, after: index + 1 })),
  ];
}

/** How many lines a diff adds and removes. */
export function diffStats(lines: readonly DiffLine[]): { added: number; removed: number } {
  return {
    added: lines.filter((line) => line.kind === 'added').length,
    removed: lines.filter((line) => line.kind === 'removed').length,
  };
}
