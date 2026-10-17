/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useRef, useState } from 'react';
import type { Live2DModelCatalogItem } from './characterAvatarClient';
import {
  LIVE2D_INSTANCE_DESTROY_OPTIONS,
  live2dModelUrl,
  loadLive2DPreviewRuntime,
  prepareLive2DTextures,
} from './live2dCharacterRenderer';

type ThumbnailApplication = {
  renderer: { width: number; height: number; resize(width: number, height: number): void };
  stage: { addChild(child: unknown): void };
  init(options: Record<string, unknown>): Promise<void>;
  render(): void;
  destroy(removeView?: boolean, options?: Record<string, boolean>): void;
};

type ThumbnailModel = {
  anchor?: { set(x: number, y?: number): void };
  scale: { set(value: number): void };
  x: number;
  y: number;
  width: number;
  height: number;
  visible?: boolean;
  renderable?: boolean;
  alpha?: number;
  textures?: Array<{ source?: { _gpuData?: Record<number, unknown> } }>;
  destroy?: (options?: Record<string, boolean>) => void;
};

type Props = {
  model: Live2DModelCatalogItem;
  className?: string;
};

export function Live2DModelThumbnail({ model, className }: Props) {
  const hostRef = useRef<HTMLSpanElement>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'unavailable'>(
    model.installed ? 'loading' : 'unavailable',
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !model.installed) {
      setState('unavailable');
      return undefined;
    }

    let disposed = false;
    let application: ThumbnailApplication | null = null;
    let liveModel: ThumbnailModel | null = null;
    let modelAttached = false;
    let applicationDestroyed = false;
    let detachedModelDestroyed = false;
    const destroyApplication = (): void => {
      if (!application || applicationDestroyed) return;
      application.destroy(false, LIVE2D_INSTANCE_DESTROY_OPTIONS);
      applicationDestroyed = true;
    };
    const destroyDetachedModel = (): void => {
      if (!liveModel || modelAttached || detachedModelDestroyed) return;
      liveModel.destroy?.(LIVE2D_INSTANCE_DESTROY_OPTIONS);
      detachedModelDestroyed = true;
    };
    const poster = document.createElement('img');
    poster.className = 'character-live2d-model-poster';
    poster.src = model.preview_url;
    poster.alt = '';
    poster.setAttribute('aria-hidden', 'true');
    const canvas = document.createElement('canvas');
    canvas.setAttribute('aria-hidden', 'true');
    canvas.className = 'character-live2d-model-canvas';
    host.replaceChildren(poster, canvas);

    const fitModel = (): void => {
      if (!application || !liveModel) return;
      const bounds = host.getBoundingClientRect();
      const width = Math.max(96, Math.round(bounds.width || host.clientWidth || 180));
      const height = Math.max(110, Math.round(bounds.height || host.clientHeight || 180));
      application.renderer.resize(width, height);
      liveModel.scale.set(1);
      const naturalWidth = Math.max(1, liveModel.width);
      const naturalHeight = Math.max(1, liveModel.height);
      const scale = Math.min((width * 0.88) / naturalWidth, (height * 0.88) / naturalHeight);
      liveModel.scale.set(Number.isFinite(scale) && scale > 0 ? scale : 1);
      liveModel.anchor?.set(0.5, 0.5);
      liveModel.x = width / 2;
      liveModel.y = height / 2;
    };

    const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fitModel);
    resizeObserver?.observe(host.parentElement ?? host);

    void loadLive2DPreviewRuntime()
      .then(async (runtime) => {
        if (disposed) return;
        application = new runtime.Application() as unknown as ThumbnailApplication;
        await application.init({
          canvas,
          autoStart: true,
          backgroundAlpha: 0,
          antialias: true,
          resolution: Math.min(window.devicePixelRatio || 1, 2),
          autoDensity: true,
          preference: 'webgl',
        });
        if (disposed) {
          destroyApplication();
          return;
        }
        liveModel = await runtime.Live2DModel.from(live2dModelUrl(`character-live2d:${model.id}`), {
          autoHitTest: false,
          autoFocus: false,
          autoUpdate: true,
        }) as ThumbnailModel;
        if (disposed) {
          destroyDetachedModel();
          destroyApplication();
          return;
        }
        prepareLive2DTextures(liveModel);
        application.stage.addChild(liveModel);
        modelAttached = true;
        liveModel.visible = true;
        liveModel.renderable = true;
        liveModel.alpha = 1;
        fitModel();
        application.render();
        requestAnimationFrame(fitModel);
        setState('ready');
      })
      .catch(() => {
        if (!disposed) setState('error');
      });

    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      destroyDetachedModel();
      destroyApplication();
      host.replaceChildren();
    };
  }, [model.id, model.installed]);

  const classes = ['character-live2d-model-thumbnail', className, `is-${state}`].filter(Boolean).join(' ');
  return <span ref={hostRef} className={classes} role={state === 'ready' ? 'img' : undefined} aria-label={state === 'ready' ? `${model.name} Live2D preview` : undefined}>
    {!model.installed ? <img className="character-live2d-model-poster" src={model.preview_url} alt="" aria-hidden="true" loading="lazy" /> : null}
  </span>;
}
