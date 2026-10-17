/**
 * Scores `text` against a typed query: every query character must appear in
 * order. Consecutive characters and characters at the start of a word score
 * higher. Returns null when the text doesn't match.
 */
export function fuzzyScore(query: string, text: string): number | null {
  const needle = query.trim().toLowerCase().replace(/\s+/g, ' ');
  if (!needle) return 0;
  const haystack = text.toLowerCase();
  const direct = haystack.indexOf(needle);
  if (direct >= 0) return 1000 - direct + (direct === 0 || /[\s/:.(-]/.test(haystack[direct - 1]) ? 200 : 0);
  let score = 0;
  let position = -1;
  let previous = -2;
  for (const character of needle) {
    if (character === ' ') continue;
    const next = haystack.indexOf(character, position + 1);
    if (next < 0) return null;
    score += next === previous + 1 ? 8 : 1;
    if (next === 0 || /[\s/:.(-]/.test(haystack[next - 1])) score += 6;
    previous = next;
    position = next;
  }
  return score;
}

/** Items that match the query, best first; ties keep their original order. */
export function fuzzyFilter<T>(items: readonly T[], query: string, text: (item: T) => string): T[] {
  if (!query.trim()) return [...items];
  return items
    .map((item, index) => ({ item, index, score: fuzzyScore(query, text(item)) }))
    .filter((entry): entry is { item: T; index: number; score: number } => entry.score !== null)
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .map((entry) => entry.item);
}
