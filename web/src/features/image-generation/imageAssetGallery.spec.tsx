import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AssetListResponse } from '../../api/client';
import { ImageAssetGallery, filterImageAssets } from './ImageAssetGallery';
import { fixture } from '../../test/fixture';

type ImageAsset = AssetListResponse['assets'][number];

const assets = fixture<ImageAsset[]>([
  {
    id: 'image:forest',
    module: 'image-generation',
    type: 'image',
    mime_type: 'image/png',
    file_name: 'forest.png',
    created_at: '2026-07-01T00:00:00Z',
    metadata: { title: 'Forest light', prompt: 'green trees', provider_key: 'flux' },
  },
  {
    id: 'image:city',
    module: 'image-generation',
    type: 'image',
    mime_type: 'image/png',
    file_name: 'city.png',
    created_at: '2026-07-02T00:00:00Z',
    metadata: { title: 'Night city', prompt: 'neon street', provider_key: 'mock' },
  },
]);

function renderGallery(node: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>
    </MantineProvider>,
  );
}

describe('ImageAssetGallery', () => {
  it('filters by search and provider', () => {
    expect(filterImageAssets(assets, 'green', '')).toEqual([assets[0]]);
    expect(filterImageAssets(assets, '', 'mock')).toEqual([assets[1]]);
  });

  it('announces filtered result counts', () => {
    renderGallery(<ImageAssetGallery assets={assets} selectedAssetId={null} onSelect={vi.fn()} />);

    expect(screen.getByRole('status')).toHaveTextContent('Showing 2 of 2 images.');
    fireEvent.change(screen.getByLabelText('Search images'), { target: { value: 'green' } });

    expect(screen.getByRole('status')).toHaveTextContent('Showing 1 of 2 images.');
    expect(screen.getByRole('button', { name: 'Select Forest light' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Select Night city' })).not.toBeInTheDocument();
  });

  it('selects a thumbnail with one accessible name', () => {
    const onSelect = vi.fn();
    renderGallery(<ImageAssetGallery assets={assets} selectedAssetId={null} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole('button', { name: 'Select Night city' }));

    expect(onSelect).toHaveBeenCalledWith('image:city');
    expect(screen.queryByRole('img', { name: 'Night city' })).not.toBeInTheDocument();
  });
});
