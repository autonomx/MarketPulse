import { useEffect, useState } from 'react';

import {
  clampLive2DZoom,
  live2dZoomPercent,
  readLive2DFraming,
  readLive2DZoom,
  setLive2DFraming,
  setLive2DZoom,
  type Live2DFraming,
  LIVE2D_ZOOM_MAX,
  LIVE2D_ZOOM_MIN,
  LIVE2D_ZOOM_STEP,
} from './live2dCharacterRenderer';

export function Live2DZoomControl() {
  const [zoom, setZoom] = useState(() => readLive2DZoom());
  const [framing, setFraming] = useState<Live2DFraming>(() => readLive2DFraming());

  useEffect(() => {
    const handleZoom = (event: Event) => {
      const value = (event as CustomEvent<{ zoom?: number }>).detail?.zoom;
      if (typeof value === 'number') setZoom(clampLive2DZoom(value));
    };
    const handleFraming = (event: Event) => {
      const value = (event as CustomEvent<{ framing?: Live2DFraming }>).detail?.framing;
      if (value === 'full' || value === 'head') setFraming(value);
    };
    window.addEventListener('omnix:character-live2d-zoom', handleZoom);
    window.addEventListener('omnix:character-live2d-framing', handleFraming);
    return () => {
      window.removeEventListener('omnix:character-live2d-zoom', handleZoom);
      window.removeEventListener('omnix:character-live2d-framing', handleFraming);
    };
  }, []);

  return (
    <div role="group" className="assistant-live2d-zoom-control" aria-label="Live2D view controls">
      <div className="assistant-live2d-framing" role="group" aria-label="Live2D framing">
        {(['full', 'head'] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={framing === value}
            onClick={() => {
              setFraming(value);
              setLive2DFraming(value);
            }}
          >
            {value === 'full' ? 'Full' : 'Head'}
          </button>
        ))}
      </div>
      <label>
        <span>Zoom</span>
        <input
          aria-label="Live2D model zoom"
          type="range"
          min={LIVE2D_ZOOM_MIN}
          max={LIVE2D_ZOOM_MAX}
          step={LIVE2D_ZOOM_STEP}
          value={zoom}
          onChange={(event) => {
            const nextZoom = clampLive2DZoom(Number(event.currentTarget.value));
            setZoom(nextZoom);
            setLive2DZoom(nextZoom);
          }}
        />
        <output>{live2dZoomPercent(zoom)}%</output>
      </label>
    </div>
  );
}
