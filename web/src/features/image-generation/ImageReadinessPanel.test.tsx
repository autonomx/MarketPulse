import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../design/theme';
import { ImageReadinessPanel } from './ImageReadinessPanel';

describe('ImageReadinessPanel', () => {
  it('links to real settings and diagnostics routes and refreshes status', () => {
    const onRefresh = vi.fn();
    render(
      <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
        <ImageReadinessPanel
          readiness={{
            status: 'ready',
            canGenerate: true,
            title: 'Image runtime ready',
            message: 'One provider is ready.',
            providerCount: 1,
            workerMode: 'worker',
          }}
          refreshing={false}
          onRefresh={onRefresh}
        />
      </MantineProvider>,
    );

    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings');
    expect(screen.getByRole('link', { name: 'Diagnostics' })).toHaveAttribute('href', '/diagnostics');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
