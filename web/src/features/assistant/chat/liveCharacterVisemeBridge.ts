 
import { DisposableStore } from '../../../app/moduleRuntime';
import { isActiveView } from '../../../app/viewApiScope';
import { CHARACTER_AVATAR_FRAME_EVENT, CHARACTER_AVATAR_RUNTIME_EVENT } from '../../../events/bus';

export type CharacterViseme = 'silence' | 'A' | 'E' | 'O' | 'U' | 'MBP' | 'FV' | 'L' | 'WQ' | 'other';

export interface TimedCharacterViseme {
  viseme: CharacterViseme;
  startMs: number;
  durationMs: number;
}

export type RuntimeAvatarPack = {
  render_mode: 'audio_envelope' | 'viseme' | 'static';
  renderer?: 'sprite' | 'live2d' | 'rive';
  rig_asset_id?: string | null;
  base_asset_id?: string | null;
  mouth_frames: Record<string, string>;
};

type RuntimeDetail = {
  display_name: string;
  avatar_pack?: RuntimeAvatarPack | null;
};

let bridgeInstalled = false;
const DEFAULT_VISEME_DURATION_MS = 90;
const STRONG_PHASE_DURATION_MS = 85;
const PEAK_PHASE_DURATION_MS = 140;
const FALLBACK_FRAME: Record<CharacterViseme, string> = {
  silence: 'closed', A: 'wide', E: 'medium', O: 'wide', U: 'small', MBP: 'closed', FV: 'small', L: 'medium', WQ: 'small', other: 'medium',
};

let runtime: RuntimeDetail | null = null;
const preloadedImages = new Map<string, HTMLImageElement>();

export function visemeSequenceFromText(text: string): CharacterViseme[] {
  const result: CharacterViseme[] = [];
  const tokens = String(text || '').match(/[a-z]+|[^a-z\s]/gi) ?? [];
  for (const token of tokens) {
    const lowered = token.toLowerCase();
    if (!/^[a-z]+$/.test(lowered)) {
      appendUnique(result, 'silence');
      continue;
    }
    for (let index = 0; index < lowered.length; index += 1) {
      const pair = lowered.slice(index, index + 2);
      if (pair === 'qu' || pair === 'wh') {
        appendUnique(result, 'WQ');
        index += 1;
        continue;
      }
      const character = lowered[index];
      appendUnique(result,
        'mbp'.includes(character) ? 'MBP'
          : 'fv'.includes(character) ? 'FV'
            : character === 'l' ? 'L'
              : 'wq'.includes(character) ? 'WQ'
                : character === 'a' ? 'A'
                  : 'eiy'.includes(character) ? 'E'
                    : character === 'o' ? 'O'
                      : character === 'u' ? 'U'
                        : 'other');
    }
    appendUnique(result, 'silence');
  }
  if (!result.length) return ['silence'];
  if (result.at(-1) !== 'silence') result.push('silence');
  return result;
}

export function fitVisemesToDuration(text: string, durationMs: number): TimedCharacterViseme[] {
  const duration = Math.max(1, durationMs);
  const sequence = visemeSequenceFromText(text);
  const weights = sequence.map((viseme) => viseme === 'silence' ? 0.45 : 1);
  const totalWeight = weights.reduce((total, weight) => total + weight, 0) || 1;
  let cursor = 0;
  return sequence.map((viseme, index) => {
    const cueDuration = index === sequence.length - 1 ? duration - cursor : duration * weights[index] / totalWeight;
    const cue = { viseme, startMs: cursor, durationMs: Math.max(1, cueDuration) };
    cursor += cueDuration;
    return cue;
  });
}

export function visemeAnimationFrameKeys(
  pack: RuntimeAvatarPack,
  previous: CharacterViseme,
  next: CharacterViseme,
  durationMs = DEFAULT_VISEME_DURATION_MS,
): string[] {
  const keys: string[] = [];
  const add = (key: string): void => {
    if (frameAssetId(pack, key) && keys.at(-1) !== key) keys.push(key);
  };

  if (next === 'silence') {
    if (previous !== 'silence') {
      add(`${previous}_strong`);
      add(`${previous}_medium`);
      add(`${previous}_soft`);
    }
    add('silence');
    return keys;
  }

  if (previous !== 'silence' && previous !== next) add(`${previous}_soft`);
  add(`${next}_soft`);
  add(`${next}_medium`);
  if (durationMs >= STRONG_PHASE_DURATION_MS) add(`${next}_strong`);
  if (durationMs >= PEAK_PHASE_DURATION_MS) add(next);
  return keys.length ? keys : [next];
}

/** Returns a function that removes it. */
export function installLiveCharacterVisemeBridge(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (bridgeInstalled) return () => undefined;
  bridgeInstalled = true;
  const store = new DisposableStore();
  store.listen(window, CHARACTER_AVATAR_RUNTIME_EVENT, (event) => {
    if (!isActiveView('chatbot')) {
      runtime = null;
      return;
    }
    runtime = (event as CustomEvent<RuntimeDetail | null>).detail;
    preloadAvatarFrames(runtime?.avatar_pack ?? null);
  });
  store.listen(window, CHARACTER_AVATAR_FRAME_EVENT, (event) => {
    if (runtime?.avatar_pack?.render_mode === 'viseme') event.stopImmediatePropagation();
  }, { capture: true });
  return () => {
    store.dispose();
    bridgeInstalled = false;
  };
}

function frameAssetId(pack: RuntimeAvatarPack, frameKey: string): string {
  if (frameKey === 'silence') {
    return pack.mouth_frames.silence || pack.mouth_frames.closed || pack.base_asset_id || '';
  }
  const direct = pack.mouth_frames[frameKey];
  if (direct) return direct;
  const baseViseme = frameKey.replace(/_(soft|medium|strong)$/, '') as CharacterViseme;
  return pack.mouth_frames[baseViseme]
    || pack.mouth_frames[FALLBACK_FRAME[baseViseme] || 'closed']
    || pack.mouth_frames.closed
    || pack.base_asset_id
    || '';
}

function preloadAvatarFrames(pack: RuntimeAvatarPack | null): void {
  preloadedImages.clear();
  if (!isActiveView('chatbot') || !pack || typeof window.Image !== 'function') return;
  const assetIds = new Set([
    ...Object.values(pack.mouth_frames),
    pack.base_asset_id || '',
  ].filter(Boolean));
  for (const assetId of assetIds) {
    const url = `/api/assets/${encodeURIComponent(assetId)}/file`;
    const image = new window.Image();
    image.decoding = 'async';
    image.src = url;
    preloadedImages.set(url, image);
  }
}

function appendUnique(values: CharacterViseme[], value: CharacterViseme): void {
  if (!values.length || values.at(-1) !== value) values.push(value);
}
