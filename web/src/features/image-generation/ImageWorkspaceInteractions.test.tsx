import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AssetListResponse, JobRecord } from '../../api/client';
import { ImageAssetGallery } from './ImageAssetGallery';
import { ImageJobList } from './ImageJobList';
import { ImageLatestResult } from './ImageLatestResult';
import { fixture } from '../../test/fixture';
import { renderWithProviders } from '../../test/renderWithProviders';

const assets: AssetListResponse['assets'] = [
  fixture({
    id: 'asset-one',
    module: 'image-generation',
    type: 'image',
    mime_type: 'image/png',
    file_name: 'one.png',
    created_at: '2026-06-14T00:00:00Z',
    metadata: {
      title: 'Mountain lake',
      prompt: 'A mountain lake at sunrise',
      provider_id: 'image:flux',
      width: 1024,
      height: 768,
    },
  }),
  fixture({
    id: 'asset-two',
    module: 'image-generation',
    type: 'image',
    mime_type: 'image/png',
    file_name: 'two.png',
    created_at: '2026-06-14T00:01:00Z',
    metadata: {
      title: 'Neon city',
      prompt: 'A neon city at night',
      provider_id: 'image:sdxl',
      width: 768,
      height: 768,
    },
  }),
];


describe('ImageAssetGallery interactions', () => {
  it('filters, switches views, selects assets, and exposes real file actions', () => {
    const onSelect = vi.fn();
    renderWithProviders(<ImageAssetGallery assets={assets} selectedAssetId="asset-one" onSelect={onSelect} />);

    expect(screen.getByRole('button', { name: 'Select Mountain lake' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Select Neon city' }));
    expect(onSelect).toHaveBeenCalledWith('asset-two');

    const openLinks = screen.getAllByRole('link', { name: 'Open' });
    const downloadLinks = screen.getAllByRole('link', { name: 'Download' });
    expect(openLinks[0]).toHaveAttribute('href', '/api/assets/asset-one/file?preview=true');
    expect(downloadLinks[0]).toHaveAttribute('href', '/api/assets/asset-one/file?download=true');

    fireEvent.change(screen.getByLabelText('Search images'), { target: { value: 'neon' } });
    expect(screen.queryByRole('button', { name: 'Select Mountain lake' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Select Neon city' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByRole('button', { name: 'Select Mountain lake' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Filter image assets by provider'), { target: { value: 'image:flux' } });
    expect(screen.getByRole('button', { name: 'Select Mountain lake' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Select Neon city' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'List view' }));
    expect(screen.getByLabelText('Image asset gallery')).toHaveClass('list');

    fireEvent.click(screen.getByRole('button', { name: 'Enlarge Mountain lake' }));
    expect(screen.getByRole('dialog', { name: 'Enlarged Mountain lake' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close enlarged image' }));
    expect(screen.queryByRole('dialog', { name: 'Enlarged Mountain lake' })).not.toBeInTheDocument();
  });
});

describe('ImageJobList interactions', () => {
  it('expands the bounded list and wires result, cancel, retry, and visible preview actions', () => {
    const onCancel = vi.fn();
    const onRetry = vi.fn();
    const onSelectAsset = vi.fn();
    const jobs = fixture<JobRecord[]>([
      {
        id: 'job-completed',
        module: 'image-generation',
        type: 'image.generate',
        status: 'completed',
        resource_class: 'gpu:image',
        priority: 0,
        created_at: '2026-06-14T00:00:00Z',
        started_at: '2026-06-14T00:00:05Z',
        completed_at: '2026-06-14T00:01:28Z',
        updated_at: '2026-06-14T00:00:00Z',
        input_payload: { prompt: 'Completed image' },
        output_refs: [{ type: 'image', asset_id: 'asset-one' }],
      },
      {
        id: 'job-running',
        module: 'image-generation',
        type: 'image.generate',
        status: 'running',
        resource_class: 'gpu:image',
        priority: 0,
        created_at: '2026-06-14T00:01:00Z',
        updated_at: '2026-06-14T00:01:00Z',
        input_payload: { prompt: 'Running image' },
      },
      {
        id: 'job-failed',
        module: 'image-generation',
        type: 'image.generate',
        status: 'failed',
        resource_class: 'gpu:image',
        priority: 0,
        created_at: '2026-06-14T00:02:00Z',
        updated_at: '2026-06-14T00:02:00Z',
        input_payload: { prompt: 'Failed image' },
      },
      {
        id: 'job-four',
        module: 'image-generation',
        type: 'image.generate',
        status: 'completed',
        resource_class: 'gpu:image',
        priority: 0,
        created_at: '2026-06-14T00:03:00Z',
        updated_at: '2026-06-14T00:03:00Z',
        input_payload: { prompt: 'Fourth image' },
      },
      {
        id: 'job-five',
        module: 'image-generation',
        type: 'image.generate',
        status: 'completed',
        resource_class: 'gpu:image',
        priority: 0,
        created_at: '2026-06-14T00:04:00Z',
        updated_at: '2026-06-14T00:04:00Z',
        input_payload: { prompt: 'Fifth image' },
      },
    ]);

    renderWithProviders(
      <ImageJobList jobs={jobs} onCancel={onCancel} onRetry={onRetry} onSelectAsset={onSelectAsset} />,
    );

    expect(screen.queryByText('Fifth image')).not.toBeInTheDocument();
    expect(screen.getByText('Generating image...')).toBeInTheDocument();
    expect(screen.getByText('Generated in 1m 23s')).toBeInTheDocument();
    expect(screen.queryByText(/0%/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show all 5' }));
    expect(screen.getByText('Fifth image')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show latest only' }));
    expect(screen.queryByText('Fifth image')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'View in Latest Result' }));
    expect(onSelectAsset).toHaveBeenCalledWith('asset-one');
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge Completed image' }));
    const dialog = screen.getByRole('dialog', { name: 'Enlarged Completed image' });
    expect(dialog).toBeInTheDocument();
    expect(dialog.parentElement).toBe(document.body);
    const imageLoader = screen.getByTestId('image-preview-loader');
    expect(screen.getByText('Loading image...')).toBeInTheDocument();
    fireEvent.load(imageLoader);
    expect(imageLoader).toHaveClass('image-preview-rendered-image', 'loaded');
    expect(screen.queryByText('Loading image...')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Completed image' })).toHaveAttribute('src', '/api/assets/asset-one/file?preview=true');
    fireEvent.error(imageLoader);
    expect(screen.getByRole('alert')).toHaveTextContent('The image could not be displayed.');
    expect(screen.getByRole('link', { name: 'Download Completed image' })).toHaveAttribute('href', '/api/assets/asset-one/file?download=true');
    fireEvent.click(screen.getByRole('button', { name: 'Close enlarged image' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledWith('job-running');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledWith('job-failed');
  });
});

describe('ImageLatestResult interactions', () => {
  it('opens the selected asset in the gallery and exposes download and open links', () => {
    const onOpenInAssets = vi.fn();
    renderWithProviders(<ImageLatestResult asset={assets[0]} onOpenInAssets={onOpenInAssets} />);

    fireEvent.click(screen.getByRole('button', { name: 'Open in Assets' }));
    expect(onOpenInAssets).toHaveBeenCalledWith('asset-one');
    expect(screen.getByRole('link', { name: 'Download Mountain lake' })).toHaveAttribute('href', '/api/assets/asset-one/file?download=true');
    expect(screen.getByRole('link', { name: 'Open Mountain lake in a new tab' })).toHaveAttribute('href', '/api/assets/asset-one/file?preview=true');
  });
});
