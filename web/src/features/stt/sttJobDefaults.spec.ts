import { describe, expect, it } from 'vitest';
import { buildSttInputPayload, buildSttStages, type SttModuleDefaults } from './sttJobDefaults';

const defaults: SttModuleDefaults = {
  providerId: 'parakeet',
  language: 'en',
  alignment: true,
  saveTranscript: true,
};

describe('STT default request helpers', () => {
  it('uses central provider and language when the job form has no override', () => {
    expect(buildSttInputPayload({ providerId: '', audioAssetId: '', sourcePath: 'input.wav', language: '' }, defaults)).toEqual({
      source_path: 'input.wav',
      provider_id: 'parakeet',
      language: 'en',
      alignment: true,
      save_transcript: true,
    });
  });

  it('preserves explicit per-job provider and language overrides', () => {
    expect(buildSttInputPayload({ providerId: 'remote-stt', audioAssetId: '', sourcePath: '', language: 'fr' }, defaults)).toMatchObject({
      provider_id: 'remote-stt',
      language: 'fr',
      source_path: null,
    });
  });

  it('only schedules optional stages enabled by central defaults', () => {
    expect(buildSttStages({ ...defaults, alignment: false }).map((stage) => stage.id)).toEqual(['transcribe', 'store-transcript']);
    expect(buildSttStages({ ...defaults, saveTranscript: false }).map((stage) => stage.id)).toEqual(['transcribe', 'align']);
  });
});
