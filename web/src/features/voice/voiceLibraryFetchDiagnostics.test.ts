import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  installVoiceLibraryFetchDiagnostics,
  resetVoiceLibraryFetchDiagnosticsForTests,
} from './voiceLibraryFetchDiagnostics';
import { pipelineFetch } from '../../api/fetchPipeline';

afterEach(() => {
  resetVoiceLibraryFetchDiagnosticsForTests();
  vi.restoreAllMocks();
});

describe('voiceLibraryFetchDiagnostics', () => {
  it('logs the resolved endpoint and discovered voice profiles', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      assets: [
        {
          id: 'voice-cloning:Maya',
          module: 'voice-cloning',
          type: 'voice_profile',
          file_name: 'Maya.wav',
          metadata: { profile_name: 'Maya' },
        },
      ],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));

    installVoiceLibraryFetchDiagnostics(fetchImpl as typeof fetch);
    const response = await pipelineFetch('/api/assets');

    expect(response.status).toBe(200);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const completed = info.mock.calls.find(([message]) => message === '[Voice Library][HTTP] request completed');
    expect(completed?.[1]).toMatchObject({
      status: 200,
      assetCount: 1,
      voiceProfileCount: 1,
      voiceProfiles: [
        {
          id: 'voice-cloning:Maya',
          name: 'Maya',
          fileName: 'Maya.wav',
        },
      ],
    });
  });

  it('logs the HTTP status and response body when the asset endpoint fails', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const fetchImpl = vi.fn(async () => new Response('Internal Server Error', { status: 500 }));

    installVoiceLibraryFetchDiagnostics(fetchImpl as typeof fetch);
    const response = await pipelineFetch('/api/assets');

    expect(response.status).toBe(500);
    const failed = error.mock.calls.find(([message]) => message === '[Voice Library][HTTP] request failed');
    expect(failed?.[1]).toMatchObject({
      status: 500,
      ok: false,
      responseBody: 'Internal Server Error',
    });
  });

  it('does not add diagnostic logging to unrelated requests', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const fetchImpl = vi.fn(async () => new Response('{}', { status: 200 }));

    installVoiceLibraryFetchDiagnostics(fetchImpl as typeof fetch);
    await pipelineFetch('/api/jobs');

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(info).not.toHaveBeenCalled();
  });
});
