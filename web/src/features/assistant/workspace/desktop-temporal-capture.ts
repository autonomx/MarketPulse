import { startTicker } from '../../../shared/timers';

export type TemporalCaptureMode = 'single' | 'temporal';
export type TemporalFrame = {
  dataUrl: string;
  capturedAtMs: number;
  width: number;
  height: number;
  sample: Uint8Array;
};

export type TemporalDesktopPayload = {
  currentImageDataUrl: string;
  historyImageDataUrl?: string;
  combinedImageDataUrl?: string;
  historyTimestamps: number[];
  captureMode: TemporalCaptureMode;
  selectedHistoryFrames: number;
};

type TemporalCaptureOptions = {
  bufferSeconds: number;
  captureFps: number;
  maxFrames: number;
  historyMaxWidth: number;
  currentMaxWidth: number;
};

const DEFAULT_OPTIONS: TemporalCaptureOptions = {
  bufferSeconds: 6,
  captureFps: 2,
  maxFrames: 12,
  historyMaxWidth: 640,
  currentMaxWidth: 1600,
};
const SAMPLE_WIDTH = 48;
const SAMPLE_HEIGHT = 27;
const DUPLICATE_THRESHOLD = 0.018;
const CHANGE_THRESHOLD = 0.035;
const TARGET_AGES_MS = [5_000, 2_000, 750, 250];

export class DesktopTemporalCapture {
  private readonly options: TemporalCaptureOptions;
  private frames: TemporalFrame[] = [];
  private stopCapture: (() => void) | null = null;

  constructor(private readonly video: HTMLVideoElement, options: Partial<TemporalCaptureOptions> = {}) {
    this.options = { ...DEFAULT_OPTIONS, ...options };
  }

  start(): void {
    if (this.stopCapture !== null) return;
    this.captureHistoryFrame();
    this.stopCapture = startTicker(
      () => this.captureHistoryFrame(),
      Math.max(100, Math.round(1_000 / this.options.captureFps)),
    );
  }

  stop(): void {
    this.stopCapture?.();
    this.stopCapture = null;
    this.frames = [];
  }

  bufferedFrameCount(): number {
    return this.frames.length;
  }

  latestActivitySample(): { sample: Uint8Array; capturedAtMs: number; width: number; height: number } | null {
    const frame = this.frames.at(-1);
    if (!frame) return null;
    return {
      sample: frame.sample.slice(),
      capturedAtMs: frame.capturedAtMs,
      width: frame.width,
      height: frame.height,
    };
  }

  async buildPayload(nowMs = performance.now()): Promise<TemporalDesktopPayload> {
    const current = captureVideoFrame(this.video, this.options.currentMaxWidth, 0.88, nowMs);
    const selected = selectTemporalFrames(this.frames, nowMs, 4, current.sample);
    if (selected.length === 0) {
      return {
        currentImageDataUrl: current.dataUrl,
        historyTimestamps: [],
        captureMode: 'single',
        selectedHistoryFrames: 0,
      };
    }

    const historyImageDataUrl = await createContactSheet(selected, nowMs);
    const combinedImageDataUrl = await createContactSheet([...selected, current], nowMs, current.capturedAtMs);
    return {
      currentImageDataUrl: current.dataUrl,
      historyImageDataUrl,
      combinedImageDataUrl,
      historyTimestamps: selected.map((frame) => relativeSeconds(frame.capturedAtMs, nowMs)),
      captureMode: 'temporal',
      selectedHistoryFrames: selected.length,
    };
  }

  private captureHistoryFrame(): void {
    try {
      const nowMs = performance.now();
      const frame = captureVideoFrame(this.video, this.options.historyMaxWidth, 0.62, nowMs);
      this.frames = retainTemporalFrames(
        [...this.frames, frame],
        nowMs,
        this.options.bufferSeconds * 1_000,
        this.options.maxFrames,
      );
    } catch {
      // The stream may not have dimensions yet or may be ending. The next interval can retry.
    }
  }
}

export function retainTemporalFrames(
  frames: TemporalFrame[],
  nowMs: number,
  bufferMs = 6_000,
  maxFrames = 12,
): TemporalFrame[] {
  const cutoff = nowMs - bufferMs;
  return frames
    .filter((frame) => frame.capturedAtMs >= cutoff && frame.capturedAtMs <= nowMs)
    .sort((left, right) => left.capturedAtMs - right.capturedAtMs)
    .slice(-maxFrames);
}

export function scoreFrameDifference(left: Uint8Array, right: Uint8Array): number {
  const length = Math.min(left.length, right.length);
  if (length === 0) return 1;
  let difference = 0;
  for (let index = 0; index < length; index += 1) {
    difference += Math.abs((left[index] ?? 0) - (right[index] ?? 0));
  }
  return difference / (length * 255);
}

export function selectTemporalFrames(
  frames: TemporalFrame[],
  nowMs: number,
  maxFrames = 4,
  currentSample?: Uint8Array,
): TemporalFrame[] {
  const eligible = retainTemporalFrames(frames, nowMs).filter((frame) => nowMs - frame.capturedAtMs >= 100);
  if (eligible.length === 0 || maxFrames <= 0) return [];

  const candidates: TemporalFrame[] = [];
  const oldestTarget = nearestFrameForAge(eligible, nowMs, TARGET_AGES_MS[0]);
  if (oldestTarget) candidates.push(oldestTarget);

  const changes = eligible.slice(1).map((frame, index) => ({
    before: eligible[index],
    after: frame,
    score: scoreFrameDifference(eligible[index]?.sample ?? new Uint8Array(), frame.sample),
  })).filter((change) => change.score >= CHANGE_THRESHOLD)
    .sort((left, right) => right.score - left.score || left.after.capturedAtMs - right.after.capturedAtMs);
  for (const change of changes) candidates.push(change.before, change.after);

  for (const targetAge of TARGET_AGES_MS.slice(1)) {
    const candidate = nearestFrameForAge(eligible, nowMs, targetAge);
    if (candidate) candidates.push(candidate);
  }

  const selected: TemporalFrame[] = [];
  for (const candidate of candidates) {
    if (selected.length >= maxFrames) break;
    if (currentSample && scoreFrameDifference(candidate.sample, currentSample) < DUPLICATE_THRESHOLD) continue;
    if (selected.some((frame) => scoreFrameDifference(frame.sample, candidate.sample) < DUPLICATE_THRESHOLD)) continue;
    selected.push(candidate);
  }
  return selected.sort((left, right) => left.capturedAtMs - right.capturedAtMs);
}

function nearestFrameForAge(frames: TemporalFrame[], nowMs: number, targetAgeMs: number): TemporalFrame | undefined {
  return frames.reduce<TemporalFrame | undefined>((best, frame) => {
    if (!best) return frame;
    const frameDistance = Math.abs((nowMs - frame.capturedAtMs) - targetAgeMs);
    const bestDistance = Math.abs((nowMs - best.capturedAtMs) - targetAgeMs);
    return frameDistance < bestDistance ? frame : best;
  }, undefined);
}

function captureVideoFrame(
  video: HTMLVideoElement,
  maxWidth: number,
  quality: number,
  capturedAtMs: number,
): TemporalFrame {
  const sourceWidth = video.videoWidth;
  const sourceHeight = video.videoHeight;
  if (!sourceWidth || !sourceHeight) throw new Error('Desktop frame is not ready');
  const scale = Math.min(1, maxWidth / sourceWidth);
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Desktop capture canvas is unavailable');
  context.drawImage(video, 0, 0, width, height);
  return {
    dataUrl: canvas.toDataURL('image/jpeg', quality),
    capturedAtMs,
    width,
    height,
    sample: sampleFrame(canvas),
  };
}

function sampleFrame(source: HTMLCanvasElement): Uint8Array {
  const canvas = document.createElement('canvas');
  canvas.width = SAMPLE_WIDTH;
  canvas.height = SAMPLE_HEIGHT;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return new Uint8Array();
  context.drawImage(source, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT);
  const pixels = context.getImageData(0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT).data;
  const sample = new Uint8Array(SAMPLE_WIDTH * SAMPLE_HEIGHT);
  for (let index = 0; index < sample.length; index += 1) {
    const offset = index * 4;
    sample[index] = Math.round(
      (pixels[offset] ?? 0) * 0.299 + (pixels[offset + 1] ?? 0) * 0.587 + (pixels[offset + 2] ?? 0) * 0.114,
    );
  }
  return sample;
}

async function createContactSheet(
  frames: TemporalFrame[],
  nowMs: number,
  currentTimestampMs?: number,
): Promise<string> {
  const images = await Promise.all(frames.map((frame) => loadImage(frame.dataUrl)));
  const columns = Math.min(2, Math.max(1, frames.length));
  const rows = Math.ceil(frames.length / columns);
  const panelWidth = 640;
  const aspectRatio = (frames[0]?.height ?? 9) / (frames[0]?.width ?? 16);
  const imageHeight = Math.max(1, Math.round(panelWidth * aspectRatio));
  const labelHeight = 30;
  const canvas = document.createElement('canvas');
  canvas.width = columns * panelWidth;
  canvas.height = rows * (imageHeight + labelHeight);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Desktop history canvas is unavailable');
  context.fillStyle = '#111';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.font = '18px sans-serif';
  context.textBaseline = 'middle';

  frames.forEach((frame, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = column * panelWidth;
    const y = row * (imageHeight + labelHeight);
    context.drawImage(images[index] as HTMLImageElement, x, y + labelHeight, panelWidth, imageHeight);
    context.fillStyle = '#111';
    context.fillRect(x, y, panelWidth, labelHeight);
    context.fillStyle = '#fff';
    const isCurrent = currentTimestampMs !== undefined && frame.capturedAtMs === currentTimestampMs;
    context.fillText(isCurrent ? 'NOW' : formatRelativeTimestamp(frame.capturedAtMs, nowMs), x + 10, y + labelHeight / 2);
  });
  return canvas.toDataURL('image/jpeg', 0.78);
}

function loadImage(dataUrl: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Desktop history frame could not be decoded'));
    image.src = dataUrl;
  });
}

export function formatRelativeTimestamp(capturedAtMs: number, nowMs: number): string {
  return `T-${Math.max(0, (nowMs - capturedAtMs) / 1_000).toFixed(2)}s`;
}

function relativeSeconds(capturedAtMs: number, nowMs: number): number {
  return Math.round(((capturedAtMs - nowMs) / 1_000) * 100) / 100;
}
