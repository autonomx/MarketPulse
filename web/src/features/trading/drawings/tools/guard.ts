// Tool code runs inside the chart's render and pointer paths. A tool that
// throws loses only the feature that failed (its shapes, handles, alert
// levels or a menu action) instead of breaking every chart on the page.

const reported = new Set<string>();

/** Runs one tool callback; on an error returns `fallback` and logs it once per tool and callback. */
export function guardToolCall<T>(toolId: string, callback: string, run: () => T, fallback: T): T {
  try {
    return run();
  } catch (error) {
    const key = `${toolId}:${callback}`;
    if (!reported.has(key)) {
      reported.add(key);
      console.error(`Drawing tool "${toolId}": ${callback} failed`, error);
    }
    return fallback;
  }
}
