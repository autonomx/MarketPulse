import { useEffect, useState } from 'react';
import {
  loadLive2DMotionOptions,
  setLive2DMotion,
  type Live2DMotionOption,
} from './live2dCharacterRenderer';

const STATIC_IDLE = 'static';

export function Live2DMotionControl({ rigAssetId }: { rigAssetId: string | null | undefined }) {
  const [options, setOptions] = useState<Live2DMotionOption[]>([]);
  const [selected, setSelected] = useState(STATIC_IDLE);

  useEffect(() => {
    let disposed = false;
    setOptions([]);
    setSelected(STATIC_IDLE);
    if (!rigAssetId) return undefined;
    void loadLive2DMotionOptions(rigAssetId).then((loaded) => {
      if (!disposed) setOptions(loaded);
    });
    return () => { disposed = true; };
  }, [rigAssetId]);

  function selectMotion(value: string): void {
    setSelected(value);
    if (!rigAssetId) return;
    if (value === STATIC_IDLE) {
      setLive2DMotion({ rigAssetId, group: null, index: -1 });
      return;
    }
    const option = options.find((candidate) => candidate.id === value);
    if (option) setLive2DMotion({ rigAssetId, group: option.group, index: option.index });
  }

  return <label className="assistant-live2d-motion-control">
    <select
      aria-label="Live2D animation"
      value={selected}
      disabled={!rigAssetId}
      onChange={(event) => selectMotion(event.currentTarget.value)}
    >
      <option value={STATIC_IDLE}>Idle</option>
      {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
    </select>
  </label>;
}
