import { DisposableStore } from '../../../app/moduleRuntime';
import type { Application } from 'pixi.js';
import type { CharacterAvatarPack, CharacterLiveCallRuntime } from './characterClient';
import type { AvatarMouthFrame } from './liveCharacterAvatarBridge';
import { ApiError } from '../../../api/errors';
import { fetchBytes } from '../../../api/transport';
import { CHARACTER_AVATAR_FRAME_EVENT, CHARACTER_AVATAR_RUNTIME_EVENT, CHARACTER_LIVE2D_FRAMING_EVENT, CHARACTER_LIVE2D_MOTION_EVENT, CHARACTER_LIVE2D_RENDER_EVENT, CHARACTER_LIVE2D_ZOOM_EVENT, emitOmnixEvent } from '../../../events/bus';

export type Live2DViseme = 'silence' | 'A' | 'E' | 'O' | 'U' | 'MBP' | 'FV' | 'L' | 'WQ' | 'other';

export type Live2DMouthShape = {
  open: number;
  form: number;
};

type Live2DRenderDetail = {
  runtime: CharacterLiveCallRuntime;
  host: HTMLElement;
};

type RigVisemeDetail = {
  viseme: Live2DViseme;
  renderer: 'sprite' | 'live2d' | 'rive';
  rigAssetId: string | null;
  durationMs: number;
};

type PixiTicker = {
  add(callback: () => void): void;
};

type PixiRenderer = {
  width: number;
  height: number;
  resize(width: number, height: number): void;
};

type PixiStage = {
  addChild(child: unknown): void;
};

type PixiApplication = {
  init(options: Record<string, unknown>): Promise<void>;
  renderer: PixiRenderer;
  stage: PixiStage;
  ticker: PixiTicker;
  render(): void;
  destroy(removeView?: boolean, options?: Record<string, boolean>): void;
};

type Live2DCoreModel = {
  _parameterIds?: unknown;
  getParameterCount?: () => number;
  getParameterMaximumValue?: (index: number) => number;
  getParameterMinimumValue?: (index: number) => number;
  getParameterIndex?: (id: unknown) => number;
  setParameterValueById?: (id: unknown, value: number, weight?: number) => void;
  setParameterValueByIndex?: (index: number, value: number, weight?: number) => void;
};

type Live2DInternalModel = {
  coreModel?: Live2DCoreModel;
  on?: (event: string, listener: () => void) => unknown;
  off?: (event: string, listener: () => void) => unknown;
  removeListener?: (event: string, listener: () => void) => unknown;
};

type Live2DModel = {
  anchor?: { set(x: number, y?: number): void };
  scale: { set(value: number): void };
  x: number;
  y: number;
  width: number;
  height: number;
  internalModel?: Live2DInternalModel;
  textures?: Array<{ source?: { _gpuData?: Record<number, unknown> } }>;
  motion?: (
    group: string,
    index: number,
    priority?: number,
    options?: { loop?: boolean; onFinish?: () => void },
  ) => Promise<boolean>;
  stopAllMotions?: () => void;
  destroy?: (options?: Record<string, boolean>) => void;
};

export type Live2DMotionOption = {
  id: string;
  group: string;
  index: number;
  label: string;
};

export type Live2DMotionSelection = {
  rigAssetId: string;
  group: string | null;
  index: number;
};

type Live2DRuntime = {
  Application: typeof Application;
  Live2DModel: {
    from(url: string, options?: Record<string, unknown>): Promise<unknown>;
  };
};

type Live2DWindow = Window & typeof globalThis & {
  Live2DCubismCore?: unknown;
};

let rendererInstalled = false;
const RIG_VISEME_EVENT = 'omnix:character-rig-viseme';
const RUNTIME_SCRIPTS = [
  '/api/character-live2d/runtime/live2dcubismcore.min.js',
] as const;
const MODEL_ENTRY_PATHS: Record<string, string> = {
  'character-live2d:open-llm-vtuber-mao-pro': 'runtime/mao_pro.model3.json',
  'character-live2d:open-llm-vtuber-shizuku': 'runtime/shizuku.model3.json',
  'character-live2d:live2d-sample-haru': 'runtime/haru.model3.json',
  'character-live2d:live2d-sample-hiyori-pro': 'runtime/hiyori_pro_t11.model3.json',
  'character-live2d:live2d-sample-epsilon-pro': 'runtime/Epsilon.model3.json',
  'character-live2d:live2d-sample-chitose': 'runtime/chitose.model3.json',
  'character-live2d:live2d-sample-koharu': 'runtime/koharu.model3.json',
  'character-live2d:live2d-sample-haruto': 'runtime/haruto.model3.json',
  'character-live2d:live2d-sample-tororo': 'runtime/tororo.model3.json',
  'character-live2d:live2d-sample-hijiki': 'runtime/hijiki.model3.json',
};
// The sample rigs use different amounts of transparent canvas padding. Keep
// the visible character at a comparable size when the shared 100% zoom is
// selected; the per-user zoom control is applied on top of this baseline.
const RIG_BASELINE_SCALE: Record<string, number> = {
  'character-live2d:open-llm-vtuber-mao-pro': 2,
  'character-live2d:open-llm-vtuber-shizuku': 1,
};
// The bundled sample rigs each expose a single looping `Idle` motion. In the
// live-call stage that reads as a repeated gesture rather than an idle avatar,
// so reserve an intentionally empty group and let Cubism update physics,
// blinking, expressions, and lip-sync parameters without replaying that clip.
// A motion group no model defines, so the idle loop stays still.
const STATIC_IDLE_MOTION_GROUP = 'omnix-static-idle';
const MOUTH_OPEN_PARAMETER_IDS = ['ParamMouthOpenY', 'PARAM_MOUTH_OPEN_Y', 'ParamA'] as const;
const MOUTH_FORM_PARAMETER_IDS = ['ParamMouthForm', 'PARAM_MOUTH_FORM'] as const;

export const LIVE2D_ZOOM_MIN = 0.75;
export const LIVE2D_ZOOM_MAX = 1.6;
export const LIVE2D_ZOOM_STEP = 0.05;
export type Live2DFraming = 'full' | 'head';

// Pixi's asset cache returns shared Texture instances for repeated loads of the
// same rig. Destroying a selector/preview must therefore release its display
// tree without destroying texture sources that may still be used by the live
// stage or another preview.
export const LIVE2D_INSTANCE_DESTROY_OPTIONS = Object.freeze({
  children: true,
  texture: false,
  textureSource: false,
  baseTexture: false,
});

let activeRigAssetId: string | null = null;
let activeAvatarCharacterId: string | null = null;
let activeAvatarPackVersion = -1;
let activeHost: HTMLElement | null = null;
let activeApplication: PixiApplication | null = null;
let activeModel: Live2DModel | null = null;
let activeResizeObserver: ResizeObserver | null = null;
let currentMouthShape: Live2DMouthShape = { open: 0, form: 0 };
let silenceTimer: ReturnType<typeof setTimeout> | null = null;
let preciseVisemeUntil = 0;
let currentZoom = 1;
let currentFraming: Live2DFraming = 'full';
let mouthAnimationFrame: number | null = null;
let activeMouthUpdateBinding: { internalModel: Live2DInternalModel; listener: () => void } | null = null;
let activeMouthOpenParameterIndices: number[] = [];
let activeMouthFormParameterIndices: number[] = [];
let renderSequence = 0;
let runtimePromise: Promise<Live2DRuntime> | null = null;
let activeMotionSelection: Live2DMotionSelection | null = null;
const motionOptionsCache = new Map<string, Promise<Live2DMotionOption[]>>();

export function loadLive2DMotionOptions(rigAssetId: string): Promise<Live2DMotionOption[]> {
  const cached = motionOptionsCache.get(rigAssetId);
  if (cached) return cached;
  const request = fetchBytes(live2dModelUrl(rigAssetId))
    .catch((error: unknown) => {
      throw error instanceof ApiError ? new Error(`Live2D model manifest could not be loaded (${error.status}).`) : error;
    })
    .then((response) => {
      return response.json() as Promise<{
        FileReferences?: { Motions?: Record<string, unknown> };
      }>;
    })
    .then((manifest) => Object.entries(manifest.FileReferences?.Motions ?? {}).flatMap(([group, definitions]) => {
      if (!Array.isArray(definitions)) return [];
      const groupLabel = group.trim()
        ? group.replace(/([a-z])([A-Z])/g, '$1 $2')
        : 'Motion';
      return definitions.map((_, index) => ({
        id: `${group}:${index}`,
        group,
        index,
        label: `${groupLabel} ${index + 1}`,
      }));
    }))
    .catch(() => []);
  motionOptionsCache.set(rigAssetId, request);
  return request;
}

export function setLive2DMotion(selection: Live2DMotionSelection): void {
  if (typeof window === 'undefined') return;
  emitOmnixEvent(CHARACTER_LIVE2D_MOTION_EVENT, selection);
}

export function live2dMouthShapeForViseme(viseme: Live2DViseme): Live2DMouthShape {
  switch (viseme) {
    case 'A': return { open: 1, form: 0.35 };
    case 'O': return { open: 0.78, form: -0.55 };
    case 'E': return { open: 0.58, form: 0.55 };
    case 'L': return { open: 0.52, form: 0.2 };
    case 'other': return { open: 0.5, form: 0 };
    case 'U': return { open: 0.42, form: -0.72 };
    case 'WQ': return { open: 0.3, form: -0.85 };
    case 'FV': return { open: 0.22, form: 0.1 };
    case 'MBP': return { open: 0.03, form: 0 };
    case 'silence':
    default:
      return { open: 0, form: 0 };
  }
}

export function live2dMouthShapeForAvatarFrame(frame: AvatarMouthFrame): Live2DMouthShape {
  switch (frame) {
    case 'small': return { open: 0.28, form: -0.25 };
    case 'medium': return { open: 0.58, form: 0.2 };
    case 'wide': return { open: 0.95, form: 0.05 };
    case 'closed':
    default:
      return { open: 0, form: 0 };
  }
}

export function clampLive2DZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(LIVE2D_ZOOM_MAX, Math.max(LIVE2D_ZOOM_MIN, value));
}

export function live2dZoomPercent(value: number): number {
  return Math.round(clampLive2DZoom(value) * 100);
}

export function readLive2DZoom(): number {
  return currentZoom;
}

export function setLive2DZoom(value: number): void {
  currentZoom = clampLive2DZoom(value);
  if (typeof window === 'undefined') return;
  emitOmnixEvent(CHARACTER_LIVE2D_ZOOM_EVENT, { zoom: currentZoom });
}

export function readLive2DFraming(): Live2DFraming {
  return currentFraming;
}

export function setLive2DFraming(value: Live2DFraming): void {
  currentFraming = value === 'head' ? 'head' : 'full';
  if (typeof window === 'undefined') return;
  emitOmnixEvent(CHARACTER_LIVE2D_FRAMING_EVENT, { framing: currentFraming });
}

export function live2dModelUrl(rigAssetId: string): string {
  const entryPath = MODEL_ENTRY_PATHS[rigAssetId] ?? 'model.model3.json';
  return `/api/character-live2d/assets/${encodeURIComponent(rigAssetId)}/${entryPath}`;
}

/** Returns a function that removes it. */
export function installLive2DCharacterRenderer(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (rendererInstalled) return () => undefined;
  rendererInstalled = true;
  const store = new DisposableStore();

  store.listen(window, CHARACTER_LIVE2D_RENDER_EVENT, (event) => {
    const detail = (event as CustomEvent<Live2DRenderDetail>).detail;
    if (!detail?.host || detail.runtime.avatar_pack?.renderer !== 'live2d') return;
    void renderLive2D(detail.runtime, detail.host);
  });
  store.listen(window, RIG_VISEME_EVENT, (event) => {
    const detail = (event as CustomEvent<RigVisemeDetail>).detail;
    if (!detail || detail.renderer !== 'live2d' || detail.rigAssetId !== activeRigAssetId) return;
    setViseme(detail.viseme, detail.durationMs);
  });
  store.listen(window, CHARACTER_AVATAR_FRAME_EVENT, (event) => {
    const detail = (event as CustomEvent<{ frame?: AvatarMouthFrame }>).detail;
    if (!detail?.frame || performance.now() < preciseVisemeUntil) return;
    currentMouthShape = live2dMouthShapeForAvatarFrame(detail.frame);
  });
  store.listen(window, CHARACTER_LIVE2D_MOTION_EVENT, (event) => {
    const detail = (event as CustomEvent<Live2DMotionSelection>).detail;
    if (!detail?.rigAssetId || detail.rigAssetId !== activeRigAssetId) return;
    activeMotionSelection = detail;
    applyLive2DMotion(detail);
  });
  store.listen(window, CHARACTER_LIVE2D_ZOOM_EVENT, (event) => {
    const zoom = (event as CustomEvent<{ zoom?: number }>).detail?.zoom;
    if (typeof zoom !== 'number') return;
    currentZoom = clampLive2DZoom(zoom);
    fitActiveModel();
  });
  store.listen(window, CHARACTER_LIVE2D_FRAMING_EVENT, (event) => {
    const framing = (event as CustomEvent<{ framing?: Live2DFraming }>).detail?.framing;
    if (framing !== 'full' && framing !== 'head') return;
    currentFraming = framing;
    fitActiveModel();
  });
  store.listen(window, CHARACTER_AVATAR_RUNTIME_EVENT, (event) => {
    const runtime = (event as CustomEvent<CharacterLiveCallRuntime | null>).detail;
    if (runtime?.avatar_pack?.renderer !== 'live2d' || !runtime.avatar_pack.rig_asset_id) {
      destroyActiveRenderer();
      return;
    }

    // The avatar bridge also emits a render event, but responding here makes a
    // Live2D-to-Live2D selection atomic. Otherwise the previous rig can remain
    // visible when the bridge reuses the existing host during a catalog refresh.
    const host = document.querySelector<HTMLElement>(
      `[data-live-chat-fullscreen-shell] .assistant-live-character-avatar`,
    ) ?? document.querySelector<HTMLElement>('.assistant-live-character-avatar');
    if (host) void renderLive2D(runtime, host);
  });
  return () => {
    destroyActiveRenderer();
    store.dispose();
    rendererInstalled = false;
  };
}

async function renderLive2D(
  runtime: CharacterLiveCallRuntime,
  host: HTMLElement,
  forceReload = false,
): Promise<void> {
  const pack = runtime.avatar_pack;
  if (!pack?.rig_asset_id || pack.renderer !== 'live2d') return;
  const packCharacterId = pack.character_id || runtime.character_id || null;
  const hostPackVersion = Number(host.dataset.live2dPackVersion ?? -1);
  const hostCharacterId = host.dataset.live2dCharacterId ?? null;
  // Runtime queries can complete out of order. Ignore an older pack before it
  // can destroy/restart the newer live rig (which made the stage appear one
  // selection behind and repeatedly restarted its first motion).
  if (
    (activeAvatarCharacterId === packCharacterId && pack.version < activeAvatarPackVersion)
    || (hostCharacterId === packCharacterId && pack.version < hostPackVersion)
  ) return;
  if (!forceReload && activeRigAssetId === pack.rig_asset_id && activeHost === host) return;

  destroyActiveRenderer();
  const sequence = ++renderSequence;
  activeRigAssetId = pack.rig_asset_id;
  activeAvatarCharacterId = packCharacterId;
  activeAvatarPackVersion = pack.version;
  activeHost = host;
  activeMotionSelection = { rigAssetId: pack.rig_asset_id, group: null, index: -1 };
  host.dataset.live2dCharacterId = packCharacterId ?? '';
  host.dataset.live2dPackVersion = String(pack.version);
  host.dataset.live2dRigAssetId = pack.rig_asset_id;
  host.dataset.renderer = 'live2d';
  host.replaceChildren();

  const canvas = document.createElement('canvas');
  canvas.className = 'assistant-live-character-live2d-canvas';
  canvas.setAttribute('aria-label', `${runtime.display_name} Live2D avatar`);
  const caption = document.createElement('figcaption');
  caption.textContent = `Loading ${runtime.display_name}…`;
  const status = document.createElement('div');
  status.className = 'assistant-live-character-live2d-status';
  status.textContent = 'Loading Live2D runtime…';
  host.append(canvas, status, caption);

  try {
    const pixi = await loadRuntime();
    if (sequence !== renderSequence || activeHost !== host) return;
    const application = new pixi.Application() as unknown as PixiApplication;
    await application.init({
      canvas,
      autoStart: true,
      backgroundAlpha: 0,
      antialias: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      autoDensity: true,
      preference: 'webgl',
    });
    const model = await pixi.Live2DModel.from(live2dModelUrl(pack.rig_asset_id), {
      autoHitTest: false,
      autoFocus: false,
      autoUpdate: true,
      motionPreload: 'NONE',
      idleMotionGroup: STATIC_IDLE_MOTION_GROUP,
    }) as unknown as Live2DModel;
    if (sequence !== renderSequence || activeHost !== host) {
      model.destroy?.(LIVE2D_INSTANCE_DESTROY_OPTIONS);
      application.destroy(false, LIVE2D_INSTANCE_DESTROY_OPTIONS);
      return;
    }

    activeApplication = application;
    activeModel = model;
    prepareLive2DTextures(model);
    application.stage.addChild(model);
    if (activeMotionSelection) applyLive2DMotion(activeMotionSelection);
    bindMouthParameterUpdate(model, sequence);
    activeResizeObserver = new ResizeObserver(() => fitModel(host, application, model));
    activeResizeObserver.observe(host);
    fitModel(host, application, model);
    status.remove();
    updateCaption(host, runtime.display_name, 'ready');
  } catch (error) {
    if (sequence !== renderSequence || activeHost !== host) return;
    status.textContent = error instanceof Error ? error.message : 'Live2D avatar could not be loaded.';
    status.dataset.state = 'error';
    updateCaption(host, runtime.display_name, 'error');
  }
}

function applyLive2DMotion(selection: Live2DMotionSelection): void {
  const model = activeModel;
  if (!model || selection.rigAssetId !== activeRigAssetId) return;
  // Cubism manifests can use an empty string as a real motion group (the Mao
  // sample stores its gesture motions under ""). Only null means the explicit
  // static-idle selection; treating "" as idle makes those motions no-op.
  if (selection.group === null || selection.index < 0) {
    model.stopAllMotions?.();
    return;
  }
  const modelAtStart = model;
  void model.motion?.(selection.group, selection.index, 2, {
    loop: false,
    onFinish: () => {
      if (activeModel !== modelAtStart || activeMotionSelection !== selection) return;
      activeMotionSelection = { rigAssetId: selection.rigAssetId, group: null, index: -1 };
      modelAtStart.stopAllMotions?.();
    },
  });
}

/**
 * Immediately replace the rig in the visible live avatar host. Avatar
 * selection calls this after the server confirms the new pack so the live
 * canvas never depends on a later bridge refresh to release its old model.
 */
export function forceRenderLive2DAvatar(runtime: CharacterLiveCallRuntime): void {
  if (typeof document === 'undefined') return;
  const hosts = Array.from(document.querySelectorAll<HTMLElement>('.assistant-live-character-avatar'));
  const visibleHost = hosts.find((candidate) => {
    const bounds = candidate.getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0;
  });
  const host = visibleHost ?? hosts[0] ?? null;
  if (!host) return;

  // Fullscreen and inline Live Voice can briefly coexist during a transition.
  // Remove every stale canvas first, then make the visible host authoritative.
  for (const candidate of hosts) {
    if (candidate !== host) candidate.replaceChildren();
  }
  host.replaceChildren();
  void renderLive2D(runtime, host, true);
}

export function prepareLive2DTextures(model: {
  textures?: Array<{ source?: { _gpuData?: Record<number, unknown> } }>;
}): void {
  // The Pixi 8 texture source creates this private map lazily, while the
  // Live2D render pipe reads it before its first bind. Initialize it at the
  // integration boundary so the first rendered frame can upload the atlas.
  for (const texture of model.textures ?? []) {
    if (texture.source && !texture.source._gpuData) texture.source._gpuData = {};
  }
}

function fitModel(host: HTMLElement, application: PixiApplication, model: Live2DModel): void {
  const width = Math.max(240, host.clientWidth || 352);
  const height = Math.max(300, host.clientHeight || 440);
  application.renderer.resize(width, height);

  model.scale.set(1);
  const naturalWidth = Math.max(1, model.width);
  const naturalHeight = Math.max(1, model.height);
  const scale = Math.min((width * 0.92) / naturalWidth, (height * 0.96) / naturalHeight);
  const framingScale = currentFraming === 'head' ? 2.2 : 1;
  const baselineScale = RIG_BASELINE_SCALE[host.dataset.live2dRigAssetId ?? ''] ?? 1;
  const fittedScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  model.scale.set(fittedScale * currentZoom * framingScale * baselineScale);
  model.anchor?.set(0.5, 0.5);
  model.x = width / 2;
  model.y = currentFraming === 'head' ? height * 1.04 : height * 0.52;
}

function setViseme(viseme: Live2DViseme, durationMs: number): void {
  currentMouthShape = live2dMouthShapeForViseme(viseme);
  if (silenceTimer !== null) clearTimeout(silenceTimer);
  const holdMs = Math.max(80, Math.min(500, durationMs + 65));
  preciseVisemeUntil = viseme === 'silence' ? 0 : performance.now() + holdMs;
  if (viseme === 'silence') return;
  silenceTimer = setTimeout(() => {
    currentMouthShape = live2dMouthShapeForViseme('silence');
    preciseVisemeUntil = 0;
    silenceTimer = null;
  }, holdMs);
}

function fitActiveModel(): void {
  if (!activeHost || !activeApplication || !activeModel) return;
  fitModel(activeHost, activeApplication, activeModel);
}

function bindMouthParameterUpdate(model: Live2DModel, sequence: number): void {
  releaseMouthParameterUpdate();
  const internalModel = model.internalModel;
  const coreModel = internalModel?.coreModel;
  activeMouthOpenParameterIndices = resolveLive2DParameterIndices(coreModel, MOUTH_OPEN_PARAMETER_IDS);
  activeMouthFormParameterIndices = resolveLive2DParameterIndices(coreModel, MOUTH_FORM_PARAMETER_IDS);

  const applyBeforeModelUpdate = (): void => {
    if (sequence !== renderSequence || activeModel !== model) return;
    const hostFrame = activeHost?.dataset.mouthFrame as AvatarMouthFrame | undefined;
    if (
      hostFrame
      && (hostFrame === 'closed' || hostFrame === 'small' || hostFrame === 'medium' || hostFrame === 'wide')
      && performance.now() >= preciseVisemeUntil
    ) {
      currentMouthShape = live2dMouthShapeForAvatarFrame(hostFrame);
    }
    applyMouthShape(model, currentMouthShape);
  };

  // Apply after motions, expressions, natural movement and physics, immediately
  // before the Cubism core renders. Earlier writes can be overwritten silently.
  if (internalModel?.on) {
    internalModel.on('beforeModelUpdate', applyBeforeModelUpdate);
    activeMouthUpdateBinding = { internalModel, listener: applyBeforeModelUpdate };
    applyBeforeModelUpdate();
  }

  // Keep a post-update write as well. Some Cubism builds restore saved
  // parameters after `beforeModelUpdate`, which otherwise makes the mouth
  // appear frozen even though audio frames are arriving correctly.
  if (mouthAnimationFrame !== null) cancelAnimationFrame(mouthAnimationFrame);
  const applyOnAnimationFrame = (): void => {
    if (sequence !== renderSequence || activeModel !== model) {
      mouthAnimationFrame = null;
      return;
    }
    applyMouthShape(model, currentMouthShape);
    mouthAnimationFrame = requestAnimationFrame(applyOnAnimationFrame);
  };
  mouthAnimationFrame = requestAnimationFrame(applyOnAnimationFrame);
}

function releaseMouthParameterUpdate(): void {
  if (activeMouthUpdateBinding) {
    const { internalModel, listener } = activeMouthUpdateBinding;
    if (internalModel.off) internalModel.off('beforeModelUpdate', listener);
    else internalModel.removeListener?.('beforeModelUpdate', listener);
    activeMouthUpdateBinding = null;
  }
  activeMouthOpenParameterIndices = [];
  activeMouthFormParameterIndices = [];
  if (mouthAnimationFrame !== null) cancelAnimationFrame(mouthAnimationFrame);
  mouthAnimationFrame = null;
}

export function resolveLive2DParameterIndices(
  coreModel: Live2DCoreModel | null | undefined,
  parameterIds: readonly string[],
): number[] {
  if (!coreModel) return [];
  const rawParameterIds = coreModel._parameterIds;
  const vectorParameterIds = rawParameterIds && typeof rawParameterIds === 'object'
    ? rawParameterIds as { getSize?: () => number; at?: (index: number) => unknown }
    : null;
  const parameterIdCount = Array.isArray(rawParameterIds)
    ? rawParameterIds.length
    : vectorParameterIds?.getSize?.() ?? 0;
  const count = coreModel.getParameterCount?.() ?? parameterIdCount;
  const resolved = new Set<number>();

  for (const parameterId of parameterIds) {
    let index: number | undefined;
    try {
      index = coreModel.getParameterIndex?.(parameterId);
    } catch {
      // The official Cubism framework expects CubismId handles rather than
      // strings. Its private ID vector is scanned below instead.
    }
    // Cubism returns a synthetic index at or above parameter count for unknown
    // IDs. Those values are writable but never affect the rendered model.
    if (typeof index === 'number' && index >= 0 && index < count) resolved.add(index);
  }

  const inspectParameterId = (parameterId: unknown, index: number): void => {
    let readableId: string;
    if (typeof parameterId === 'string') {
      readableId = parameterId;
    } else if (typeof (parameterId as { getString?: () => unknown })?.getString === 'function') {
      const value = (parameterId as { getString: () => unknown }).getString();
      readableId = typeof value === 'string'
        ? value
        : typeof (value as { s?: unknown })?.s === 'string'
          ? (value as { s: string }).s
          : String(value);
    } else {
      readableId = String(parameterId);
    }
    if (parameterIds.some((candidate) => candidate.toLocaleLowerCase() === readableId.toLocaleLowerCase())) {
      resolved.add(index);
    }
  };
  if (Array.isArray(rawParameterIds)) {
    rawParameterIds.forEach(inspectParameterId);
  } else if (vectorParameterIds?.at) {
    for (let index = 0; index < parameterIdCount; index += 1) {
      inspectParameterId(vectorParameterIds.at(index), index);
    }
  }

  return [...resolved];
}

function applyMouthShape(model: Live2DModel, shape: Live2DMouthShape): void {
  const coreModel = model.internalModel?.coreModel;
  if (!coreModel) return;
  if (coreModel.setParameterValueByIndex && activeMouthOpenParameterIndices.length) {
    for (const parameterIndex of activeMouthOpenParameterIndices) {
      const minimum = coreModel.getParameterMinimumValue?.(parameterIndex) ?? 0;
      const maximum = coreModel.getParameterMaximumValue?.(parameterIndex) ?? 1;
      const value = minimum + shape.open * (maximum - minimum);
      coreModel.setParameterValueByIndex(parameterIndex, value, 1);
    }
  } else if (coreModel.setParameterValueById) {
    for (const parameterId of MOUTH_OPEN_PARAMETER_IDS) {
      try { coreModel.setParameterValueById(parameterId, shape.open, 1); } catch { /* legacy runtime fallback */ }
    }
  }
  if (coreModel.setParameterValueByIndex && activeMouthFormParameterIndices.length) {
    for (const parameterIndex of activeMouthFormParameterIndices) {
      coreModel.setParameterValueByIndex(parameterIndex, shape.form, 1);
    }
  } else if (coreModel.setParameterValueById) {
    for (const parameterId of MOUTH_FORM_PARAMETER_IDS) {
      try { coreModel.setParameterValueById(parameterId, shape.form, 1); } catch { /* optional parameter */ }
    }
  }
}

function updateCaption(host: HTMLElement, displayName: string, state: 'ready' | 'error'): void {
  const caption = host.querySelector<HTMLElement>('figcaption');
  if (!caption) return;
  caption.textContent = state === 'error' ? `${displayName} Live2D unavailable` : displayName;
}

function loadRuntime(): Promise<Live2DRuntime> {
  if (runtimePromise) return runtimePromise;
  runtimePromise = (async () => {
    for (const source of RUNTIME_SCRIPTS) await loadScript(source);
    if (!(window as Live2DWindow).Live2DCubismCore) {
      throw new Error('Installed Live2D Cubism runtime is incomplete.');
    }
    // Pixi loads only when a Live2D avatar is shown (WP-9.9), with its
    // eval-free shader path so the ingress CSP can omit 'unsafe-eval' (WP-11.3).
    const pixi = await import('pixi.js');
    await import('pixi.js/unsafe-eval');
    const live2d = await import('untitled-pixi-live2d-engine/cubism');
    pixi.extensions.add(live2d.Live2DPlugin);
    live2d.configureCubismSDK({ memorySizeMB: 64 });
    return { Application: pixi.Application, Live2DModel: live2d.Live2DModel };
  })().catch((error) => {
    runtimePromise = null;
    throw error;
  });
  return runtimePromise;
}

/** Reuse the installed Live2D runtime for lightweight model previews. */
export function loadLive2DPreviewRuntime(): Promise<Live2DRuntime> {
  return loadRuntime();
}

function loadScript(source: string): Promise<void> {
  const existing = document.querySelector<HTMLScriptElement>(`script[data-omnix-live2d-source="${source}"]`);
  if (existing?.dataset.loaded === 'true') return Promise.resolve();
  if (existing) {
    return new Promise((resolve, reject) => {
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error(`Could not load ${source}`)), { once: true });
    });
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = source;
    script.async = false;
    script.dataset.omnixLive2dSource = source;
    script.addEventListener('load', () => {
      script.dataset.loaded = 'true';
      resolve();
    }, { once: true });
    script.addEventListener('error', () => reject(new Error(`Could not load ${source}`)), { once: true });
    document.head.append(script);
  });
}

function destroyActiveRenderer(): void {
  renderSequence += 1;
  if (silenceTimer !== null) clearTimeout(silenceTimer);
  silenceTimer = null;
  releaseMouthParameterUpdate();
  preciseVisemeUntil = 0;
  currentMouthShape = { open: 0, form: 0 };
  activeResizeObserver?.disconnect();
  activeResizeObserver = null;
  if (activeApplication) {
    activeApplication.destroy(false, LIVE2D_INSTANCE_DESTROY_OPTIONS);
  } else {
    activeModel?.destroy?.(LIVE2D_INSTANCE_DESTROY_OPTIONS);
  }
  activeModel = null;
  activeApplication = null;
  activeRigAssetId = null;
  activeAvatarCharacterId = null;
  activeAvatarPackVersion = -1;
  activeHost = null;
}

export function isLive2DPack(pack: CharacterAvatarPack | null | undefined): boolean {
  return pack?.renderer === 'live2d' && Boolean(pack.rig_asset_id);
}
