import { afterEach, expect, test, vi } from 'vitest';
import { voiceApiClient } from './voiceClient';

afterEach(() => {
  vi.unstubAllGlobals();
});

test('Voice Studio lists its bounded voice job summaries', async () => {
  const requested: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    requested.push(`${url.pathname}${url.search}`);
    return Response.json({ jobs: [{ id: 'job:voice', module: 'voice' }], has_more: false });
  }));

  const response = await voiceApiClient.listVoiceJobSummaries();

  expect(response.jobs.map((job) => job.id)).toEqual(['job:voice']);
  expect(requested).toEqual(['/api/jobs/voice-summaries?limit=40']);
});
