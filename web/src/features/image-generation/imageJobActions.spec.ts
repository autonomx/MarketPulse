import { describe, expect, it } from 'vitest';
import type { JobRecord } from '../../api/client';
import { canCancelImageJob, canRetryImageJob, firstImageAssetId } from './ImageJobList';

const job = (status: JobRecord['status'], output_refs: JobRecord['output_refs'] = []) => ({ status, output_refs }) as JobRecord;

describe('image job actions', () => {
  it('separates cancel and retry states', () => {
    expect(canCancelImageJob(job('running'))).toBe(true);
    expect(canCancelImageJob(job('completed'))).toBe(false);
    expect(canRetryImageJob(job('failed'))).toBe(true);
    expect(canRetryImageJob(job('queued'))).toBe(false);
  });

  it('finds image output assets', () => {
    expect(firstImageAssetId(job('completed', [{ type: 'image', asset_id: 'image:result' }]))).toBe('image:result');
    expect(firstImageAssetId(job('completed', [{ type: 'audio', asset_id: 'audio:result' }]))).toBeUndefined();
  });
});
