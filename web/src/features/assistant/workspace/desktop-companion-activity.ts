export type DesktopActivity =
  | 'static'
  | 'micro_change'
  | 'translation_like'
  | 'localized_change'
  | 'continuous_motion'
  | 'full_scene_change'
  | 'unknown';

export type DesktopActivityHypothesis =
  | 'none'
  | 'likely_scroll'
  | 'likely_typing'
  | 'likely_navigation'
  | 'likely_app_switch'
  | 'likely_media';

export type DesktopActivityReading = {
  activity: DesktopActivity;
  hypothesis: DesktopActivityHypothesis;
  confidence: number;
  changedRatio: number;
  meanDifference: number;
  horizontalShift: number;
  verticalShift: number;
  focus: number;
  capturedAtMs: number;
};

export type DesktopBehaviorReading = {
  currentPattern: 'starting' | 'settled' | 'browsing' | 'rapid_switching' | 'exploring' | 'watching' | 'typing' | 'mixed';
  settledSeconds: number;
  browsingPace: number;
  rapidBrowsing: boolean;
  likelyTyping: boolean;
  likelyMedia: boolean;
  transition: string | null;
  sampleCount: number;
};

export type ActivityClassifierOptions = {
  width: number;
  height: number;
  changedPixelThreshold: number;
  staticThreshold: number;
  microThreshold: number;
  sceneThreshold: number;
  maxShift: number;
};

const DEFAULT_OPTIONS: ActivityClassifierOptions = {
  width: 48,
  height: 27,
  changedPixelThreshold: 18 / 255,
  staticThreshold: 0.012,
  microThreshold: 0.045,
  sceneThreshold: 0.46,
  maxShift: 4,
};

const clamp = (value: number, minimum = 0, maximum = 1) => Math.max(minimum, Math.min(maximum, value));

export function classifyDesktopActivity(
  previous: Uint8Array | null,
  current: Uint8Array,
  capturedAtMs: number,
  options: Partial<ActivityClassifierOptions> = {},
): DesktopActivityReading {
  const config = { ...DEFAULT_OPTIONS, ...options };
  if (!previous || previous.length !== current.length || current.length !== config.width * config.height) {
    return signal('unknown', 'none', 0, 0, 0, 0, 0, 0, capturedAtMs);
  }

  let changed = 0;
  let difference = 0;
  let sumX = 0;
  let sumY = 0;
  let sumX2 = 0;
  let sumY2 = 0;
  const threshold = config.changedPixelThreshold * 255;
  for (let index = 0; index < current.length; index += 1) {
    const delta = Math.abs((previous[index] ?? 0) - (current[index] ?? 0));
    difference += delta;
    if (delta < threshold) continue;
    changed += 1;
    const x = index % config.width;
    const y = Math.floor(index / config.width);
    sumX += x;
    sumY += y;
    sumX2 += x * x;
    sumY2 += y * y;
  }
  const changedRatio = changed / current.length;
  const meanDifference = difference / (current.length * 255);
  const focus = changed > 1
    ? clamp(1 - ((variance(sumX, sumX2, changed) / (config.width * config.width))
      + (variance(sumY, sumY2, changed) / (config.height * config.height))))
    : changed === 1 ? 1 : 0;

  if (meanDifference < config.staticThreshold || changedRatio < 0.025) {
    return signal('static', 'none', 0.95, changedRatio, meanDifference, 0, 0, focus, capturedAtMs);
  }

  const translation = estimateTranslation(previous, current, config.width, config.height, config.maxShift);
  if (translation.confidence >= 0.58 && changedRatio < 0.82) {
    return signal(
      'translation_like',
      'likely_scroll',
      clamp(translation.confidence),
      changedRatio,
      meanDifference,
      translation.dx,
      translation.dy,
      focus,
      capturedAtMs,
    );
  }

  if (meanDifference >= config.sceneThreshold || changedRatio >= 0.82) {
    return signal('full_scene_change', 'likely_app_switch', clamp(0.65 + meanDifference * 0.3), changedRatio, meanDifference, 0, 0, focus, capturedAtMs);
  }

  if (meanDifference < config.microThreshold) {
    const likelyTyping = focus >= 0.62 && changedRatio <= 0.14;
    return signal(
      likelyTyping ? 'localized_change' : 'micro_change',
      likelyTyping ? 'likely_typing' : 'none',
      likelyTyping ? 0.58 : 0.72,
      changedRatio,
      meanDifference,
      0,
      0,
      focus,
      capturedAtMs,
    );
  }

  if (focus <= 0.48 && changedRatio >= 0.16 && changedRatio <= 0.7) {
    return signal('continuous_motion', 'likely_media', 0.58, changedRatio, meanDifference, 0, 0, focus, capturedAtMs);
  }

  return signal('localized_change', 'likely_navigation', clamp(0.5 + changedRatio * 0.35), changedRatio, meanDifference, 0, 0, focus, capturedAtMs);
}

function signal(
  activity: DesktopActivity,
  hypothesis: DesktopActivityHypothesis,
  confidence: number,
  changedRatio: number,
  meanDifference: number,
  horizontalShift: number,
  verticalShift: number,
  focus: number,
  capturedAtMs: number,
): DesktopActivityReading {
  return {
    activity,
    hypothesis,
    confidence: clamp(confidence),
    changedRatio: clamp(changedRatio),
    meanDifference: clamp(meanDifference),
    horizontalShift,
    verticalShift,
    focus: clamp(focus),
    capturedAtMs,
  };
}

function variance(sum: number, sumSquares: number, count: number): number {
  if (count <= 1) return 0;
  const mean = sum / count;
  return Math.max(0, sumSquares / count - mean * mean);
}

function estimateTranslation(
  previous: Uint8Array,
  current: Uint8Array,
  width: number,
  height: number,
  maxShift: number,
): { dx: number; dy: number; confidence: number } {
  let baseline = 0;
  for (let index = 0; index < current.length; index += 1) baseline += Math.abs((previous[index] ?? 0) - (current[index] ?? 0));
  let best = baseline;
  let bestDx = 0;
  let bestDy = 0;
  for (let dy = -maxShift; dy <= maxShift; dy += 1) {
    for (let dx = -maxShift; dx <= maxShift; dx += 1) {
      if (dx === 0 && dy === 0) continue;
      let difference = 0;
      let compared = 0;
      for (let y = Math.max(0, -dy); y < Math.min(height, height - dy); y += 1) {
        for (let x = Math.max(0, -dx); x < Math.min(width, width - dx); x += 1) {
          difference += Math.abs((previous[y * width + x] ?? 0) - (current[(y + dy) * width + (x + dx)] ?? 0));
          compared += 1;
        }
      }
      if (!compared) continue;
      const normalized = difference * (current.length / compared);
      if (normalized < best) {
        best = normalized;
        bestDx = dx;
        bestDy = dy;
      }
    }
  }
  const improvement = baseline > 0 ? clamp((baseline - best) / baseline) : 0;
  return { dx: bestDx, dy: bestDy, confidence: improvement };
}

export class DesktopBehaviorTracker {
  private readonly history: DesktopActivityReading[] = [];
  private lastActiveAtMs = 0;
  private previousHypothesis: DesktopActivityHypothesis = 'none';

  record(signalValue: DesktopActivityReading): DesktopBehaviorReading {
    this.history.push(signalValue);
    if (this.history.length > 20) this.history.splice(0, this.history.length - 20);
    const quiet = signalValue.activity === 'static' || signalValue.activity === 'micro_change';
    if (!quiet) this.lastActiveAtMs = signalValue.capturedAtMs;
    const state = this.snapshot(signalValue.capturedAtMs);
    this.previousHypothesis = signalValue.hypothesis;
    return state;
  }

  snapshot(nowMs: number): DesktopBehaviorReading {
    const recent = this.history.slice(-6);
    const active = recent.filter((item) => !['static', 'micro_change'].includes(item.activity));
    const browsing = recent.filter((item) => ['likely_scroll', 'likely_navigation', 'likely_app_switch'].includes(item.hypothesis));
    const typing = recent.filter((item) => item.hypothesis === 'likely_typing' && item.confidence >= 0.55);
    const media = recent.filter((item) => item.hypothesis === 'likely_media' && item.confidence >= 0.55);
    const quietTail = this.history.slice(-4);
    const settled = quietTail.length === 4 && quietTail.every((item) => item.activity === 'static' || item.activity === 'micro_change');
    const rapidBrowsing = browsing.length >= 3;
    const likelyTyping = typing.length >= 2;
    const likelyMedia = media.length >= 3;
    let currentPattern: DesktopBehaviorReading['currentPattern'] = 'starting';
    if (likelyTyping) currentPattern = 'typing';
    else if (likelyMedia) currentPattern = 'watching';
    else if (settled) currentPattern = 'settled';
    else if (rapidBrowsing && recent.filter((item) => item.hypothesis === 'likely_app_switch').length >= 2) currentPattern = 'rapid_switching';
    else if (rapidBrowsing) currentPattern = 'browsing';
    else if (browsing.length >= 2) currentPattern = 'exploring';
    else if (recent.length >= 3) currentPattern = 'mixed';

    const latest = recent.at(-1);
    const transition = transitionFor(this.previousHypothesis, latest?.hypothesis ?? 'none', settled);
    return {
      currentPattern,
      settledSeconds: settled ? Math.max(0, (nowMs - this.lastActiveAtMs) / 1000) : 0,
      browsingPace: recent.length ? clamp(active.length / recent.length) : 0,
      rapidBrowsing,
      likelyTyping,
      likelyMedia,
      transition,
      sampleCount: this.history.length,
    };
  }

  reset(): void {
    this.history.length = 0;
    this.lastActiveAtMs = 0;
    this.previousHypothesis = 'none';
  }
}

function transitionFor(previous: DesktopActivityHypothesis, current: DesktopActivityHypothesis, settled: boolean): string | null {
  if (settled) return 'settled_down';
  if (current === previous) return null;
  if (current === 'likely_scroll') return 'started_scrolling';
  if (current === 'likely_typing') return 'started_typing';
  if (current === 'likely_navigation') return 'navigated';
  if (current === 'likely_app_switch') return 'switched_app';
  if (current === 'likely_media') return 'started_watching';
  return null;
}
