import { describe, expect, it, vi } from 'vitest';
import { loadSettingsProfile } from './settingsApi';

describe('settings profile API', () => {
  it('retries a transient server failure before reporting settings unavailable', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('{}', { status: 500 }))
      .mockResolvedValueOnce(Response.json({
        success: true,
        provider: 'lmstudio',
        audio_provider_tts: 'tts',
        audio_provider_stt: 'stt',
        settings: {},
      }));

    const result = await loadSettingsProfile(fetcher);

    expect(result.legacy.provider).toBe('lmstudio');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
