import { afterEach, describe, expect, it, vi } from 'vitest';
import { downloadJson, downloadUrl } from './download';

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('downloads', () => {
  it('clicks a temporary link and leaves nothing behind', () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.isConnected).toBe(true);
      expect(this.download).toBe('take.wav');
    });

    downloadUrl('data:audio/wav;base64,AAAA', 'take.wav');

    expect(click).toHaveBeenCalledOnce();
    expect(document.querySelector('a[download]')).toBeNull();
  });

  it('saves JSON through an object URL that is released afterwards', () => {
    vi.useFakeTimers();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const create = vi.fn(() => 'blob:export');
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });

    downloadJson({ ok: true }, 'export.json');

    expect(create).toHaveBeenCalledWith(expect.any(Blob));
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:export');
  });
});
