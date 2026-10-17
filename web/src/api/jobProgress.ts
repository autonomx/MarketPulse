/** A job's progress as a whole percentage; 0 until it reports a total. */
export function jobProgressPercent(progress: { current?: number; total?: number } | null | undefined): number {
  const total = progress?.total ?? 0;
  if (total <= 0) return 0;
  return Math.min(100, Math.round(((progress?.current ?? 0) / total) * 100));
}
