import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { readSearchParam, useSearchParam } from './useSearchParam';

describe('useSearchParam', () => {
  it('reads, writes and clears a selection in the URL without a router', () => {
    window.history.replaceState(null, '', '/chatbot?session=chat%3A1&other=kept');
    const { result } = renderHook(() => useSearchParam('session'));
    expect(result.current[0]).toBe('chat:1');

    act(() => result.current[1]('chat:2'));
    expect(result.current[0]).toBe('chat:2');
    expect(new URLSearchParams(window.location.search).get('other')).toBe('kept');

    act(() => result.current[1](null));
    expect(result.current[0]).toBeNull();
    expect(window.location.search).toBe('?other=kept');
  });

  it('reads values the router quoted because they look like JSON', () => {
    window.history.replaceState(null, '', '/audiobook?project=%22123%22');
    expect(readSearchParam('project')).toBe('123');
    window.history.replaceState(null, '', '/audiobook?project=%22');
    expect(readSearchParam('project')).toBe('"');
  });
});
