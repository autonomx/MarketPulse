import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { AssetListResponse } from '../../api/client';
import { ImageLatestResult } from './ImageLatestResult';
import { fixture } from '../../test/fixture';

type ImageAsset = AssetListResponse['assets'][number];

const asset = fixture<ImageAsset>({
  id: 'image:night',
  module: 'image-generation',
  type: 'image',
  mime_type: 'image/png',
  file_name: 'night.png',
  created_at: '2026-07-05T00:00:00Z',
  metadata: {
    title: 'Night harbor',
    width: 1024,
    height: 768,
    provider_key: 'flux_klein',
  },
});

describe('ImageLatestResult', () => {
  it('announces completed results and exposes asset-id actions', () => {
    render(<MantineProvider><ImageLatestResult asset={asset} /></MantineProvider>);

    const region = screen.getByRole('region', { name: 'Latest result' });
    expect(region).toHaveAttribute('aria-live', 'polite');
    expect(region).toHaveAttribute('aria-atomic', 'true');
    expect(screen.getByRole('img', { name: 'Night harbor' })).toHaveAttribute(
      'src',
      '/api/assets/image%3Anight/file?preview=true',
    );
    expect(screen.getByRole('link', { name: 'Open Night harbor in a new tab' })).toHaveAttribute(
      'href',
      '/api/assets/image%3Anight/file?preview=true',
    );
    expect(screen.getByRole('link', { name: 'Download Night harbor' })).toHaveAttribute(
      'href',
      '/api/assets/image%3Anight/file?download=true',
    );
    expect(region).not.toHaveTextContent('private/night.png');
  });

  it('opens and closes an enlarged image preview when the result image is clicked', () => {
    render(<MantineProvider><ImageLatestResult asset={asset} /></MantineProvider>);

    fireEvent.click(screen.getByRole('button', { name: 'Enlarge Night harbor' }));

    const dialog = screen.getByRole('dialog', { name: 'Enlarged Night harbor' });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByTestId('image-preview-loader')).toHaveAttribute(
      'src',
      '/api/assets/image%3Anight/file?preview=true',
    );
    expect(screen.getByLabelText('Image zoom level')).toHaveTextContent('100%');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(screen.getByLabelText('Image zoom level')).toHaveTextContent('125%');
    fireEvent.click(screen.getByRole('button', { name: 'Reset zoom' }));
    expect(screen.getByLabelText('Image zoom level')).toHaveTextContent('100%');

    fireEvent.click(screen.getByRole('button', { name: 'Close enlarged image' }));

    expect(screen.queryByRole('dialog', { name: 'Enlarged Night harbor' })).not.toBeInTheDocument();
  });

  it('announces the empty state', () => {
    render(<MantineProvider><ImageLatestResult /></MantineProvider>);

    expect(screen.getByRole('status')).toHaveTextContent('Generate an image to see the latest result here.');
  });
});
