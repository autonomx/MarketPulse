import { describe, expect, it } from 'vitest';
import { selectedImageModel, toImageModelStatusView } from './ImageModelSelectorControl';

describe('image model status view', () => {
  it('fills identity and state the status route leaves out', () => {
    const view = toImageModelStatusView({
      ok: true,
      enabled: true,
      models: [
        { key: 'flux_klein', label: 'FLUX.2 [klein] 4B', loaded: true },
        { provider: 'krea2_turbo', model: 'Krea 2 Turbo', state: 'downloading', gated: true },
      ],
    });

    expect(view).toMatchObject({ provider: '', loaded: false, state: 'unloaded' });
    expect(view.models).toEqual([
      { key: 'flux_klein', label: 'FLUX.2 [klein] 4B', provider: 'flux_klein', model: 'FLUX.2 [klein] 4B', loaded: true, state: 'loaded' },
      { provider: 'krea2_turbo', model: 'Krea 2 Turbo', loaded: false, state: 'downloading', gated: true },
    ]);
    expect(selectedImageModel(view, 'flux_klein')?.loaded).toBe(true);
  });
});
