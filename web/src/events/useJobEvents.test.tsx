import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixEventClient, RESYNC_EVENT } from './eventClient';
import { useJobEventRefresh } from './useJobEvents';
import { createTestQueryClient } from '../test/renderWithProviders';

afterEach(() => {
  vi.restoreAllMocks();
});

function captureSubscriptions() {
  const handlers = new Map<string, Array<(payload: unknown) => void>>();
  vi.spyOn(omnixEventClient, 'subscribe').mockImplementation((eventName, handler) => {
    handlers.set(eventName, [...(handlers.get(eventName) ?? []), handler as (payload: unknown) => void]);
    return () => undefined;
  });
  return (eventName: string, payload: unknown) => handlers.get(eventName)?.forEach((handler) => handler(payload));
}

function renderWithClient(callback: () => void) {
  const client = createTestQueryClient();
  const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  renderHook(callback, { wrapper });
  return invalidate;
}

describe('useJobEventRefresh', () => {
  it('refetches only for the watched job, and always after a resync', () => {
    const emit = captureSubscriptions();
    const queryKeys = [['feature', 'chatbot', 'generation-job', 'job:1']];
    const invalidate = renderWithClient(() => useJobEventRefresh(queryKeys, { jobId: 'job:1' }));

    emit('job.completed', { job_id: 'job:2' });
    expect(invalidate).not.toHaveBeenCalled();

    emit('job.completed', { job_id: 'job:1' });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys[0] });

    invalidate.mockClear();
    emit(RESYNC_EVENT, {});
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('does not subscribe while disabled', () => {
    captureSubscriptions();
    renderWithClient(() => useJobEventRefresh([['platform', 'jobs']], { enabled: false }));
    expect(omnixEventClient.subscribe).not.toHaveBeenCalled();
  });
});
