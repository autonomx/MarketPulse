import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const context = vi.hoisted(() => ({
  state: { dirtyPaths: [] as string[], status: 'idle', message: null as string | null, server: {}, draft: {} },
  dispatch: vi.fn(),
}));
const api = vi.hoisted(() => ({
  createSettingsSaveRequest: vi.fn(() => ({ request: true })),
  saveSettingsProfile: vi.fn(async () => undefined),
  loadSettingsProfile: vi.fn(async () => ({ profile: { saved: true } })),
  SettingsProfileApiError: class extends Error { status = 0; },
}));
vi.mock('../src/features/settings/SettingsProfileContext', () => ({ useSettingsProfileContext: () => context }));
vi.mock('../src/features/settings/settingsApi', () => api);

const { SettingsSaveBar } = await import('./SettingsSaveBar');

afterEach(() => {
  cleanup();
  context.state = { dirtyPaths: [], status: 'idle', message: null, server: {}, draft: {} };
  context.dispatch.mockReset();
});

describe('settings save bar', () => {
  it('stays out of the way while nothing waits to be saved', () => {
    const { container } = render(<SettingsSaveBar />);
    expect(container).toBeEmptyDOMElement();
  });

  it('counts unsaved changes, saves them, and can discard them', async () => {
    context.state.dirtyPaths = ['providers.llm', 'routing.fast'];
    render(<SettingsSaveBar />);

    expect(screen.getByRole('status')).toHaveTextContent('2 unsaved changes');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save changes' })));
    expect(api.saveSettingsProfile).toHaveBeenCalledWith({ request: true });
    expect(context.dispatch).toHaveBeenCalledWith({ type: 'saved', document: { saved: true } });

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(context.dispatch).toHaveBeenCalledWith({ type: 'discard' });
  });

  it('reports a failed save', () => {
    context.state.status = 'error';
    context.state.message = 'Settings could not be saved.';
    render(<SettingsSaveBar />);

    expect(screen.getByRole('alert')).toHaveTextContent('Settings could not be saved.');
  });
});
