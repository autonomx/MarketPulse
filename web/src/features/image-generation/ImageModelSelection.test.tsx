import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../design/theme';
import { ImageModelControl, type ImageModelStatusView } from './ImageModelSelectorControl';
import {
  buildImageGenerateInput,
  imageRequestDefaultValues,
  resolveImageRequestDefaults,
} from './imageRequestModel';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function renderControl(
  status: ImageModelStatusView,
  overrides: Partial<Parameters<typeof ImageModelControl>[0]> = {},
) {
  const props = {
    status,
    selectedProvider: status.provider,
    statusLoading: false,
    action: null,
    onSelect: vi.fn(),
    onDownload: vi.fn(),
    onLoad: vi.fn(),
    onUnload: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  };
  render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <ImageModelControl {...props} />
    </MantineProvider>,
  );
  return props;
}

it('selects another image model without downloading or loading it', () => {
  const onSelect = vi.fn();
  const onDownload = vi.fn();
  const onLoad = vi.fn();
  const status: ImageModelStatusView = {
    ok: true,
    service: 'image',
    enabled: true,
    provider: 'flux_klein',
    model: 'FLUX.2 [klein] 4B',
    loaded: false,
    state: 'unloaded',
    local_model: { complete: true },
    models: [
      {
        provider: 'flux_klein',
        model: 'FLUX.2 [klein] 4B',
        loaded: false,
        state: 'unloaded',
        local_model: { complete: true },
      },
      {
        provider: 'krea2_turbo',
        model: 'Krea 2 Turbo',
        loaded: false,
        state: 'unloaded',
        supports_download: true,
        local_model: { complete: false, missing: ['model_index.json'] },
      },
    ],
  };

  renderControl(status, { onSelect, onDownload, onLoad });

  fireEvent.change(screen.getByRole('combobox', { name: 'Image model' }), {
    target: { value: 'krea2_turbo' },
  });

  expect(onSelect).toHaveBeenCalledWith('krea2_turbo');
  expect(onDownload).not.toHaveBeenCalled();
  expect(onLoad).not.toHaveBeenCalled();
});

it('shows Download Model and starts the service automatically before downloading', async () => {
  const onRefresh = vi.fn();
  const onDownload = vi.fn();
  const fetchMock = vi.fn(async () => Response.json({ ok: true, state: 'unloaded' }));
  vi.stubGlobal('fetch', fetchMock);

  const status: ImageModelStatusView = {
    ok: false,
    service: 'image',
    enabled: true,
    provider: 'z_image_turbo',
    model: 'Z-Image Turbo',
    loaded: false,
    state: 'unavailable',
    error: 'image_service_unreachable',
    supports_download: true,
    local_model: { complete: false, missing: ['model_index.json'] },
  };

  renderControl(status, { onRefresh, onDownload });

  fireEvent.click(screen.getByRole('button', { name: 'Download Model' }));

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith('/api/image-generation/service/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: 'z_image_turbo' }),
    });
    expect(onDownload).toHaveBeenCalledWith('z_image_turbo', undefined);
  });
  expect(onRefresh).not.toHaveBeenCalled();
});

it('passes a masked one-time token when downloading a gated model', () => {
  const onDownload = vi.fn();
  const status: ImageModelStatusView = {
    ok: false,
    service: 'image',
    enabled: true,
    provider: 'krea2_turbo',
    model: 'Krea 2 Turbo',
    loaded: false,
    state: 'unloaded',
    supports_download: true,
    gated: true,
    local_model: { complete: false, missing: ['model_index.json'] },
  };

  renderControl(status, { onDownload });

  const tokenInput = screen.getByLabelText('Hugging Face token');
  expect(tokenInput).toHaveAttribute('type', 'password');
  fireEvent.change(tokenInput, { target: { value: '  hf_test_token  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Download Model' }));

  expect(onDownload).toHaveBeenCalledWith('krea2_turbo', 'hf_test_token');
  expect(screen.getByText(/Used only for this download and not stored/)).toBeInTheDocument();
});

it('shows Load Model and starts the service automatically for downloaded files', async () => {
  const onRefresh = vi.fn();
  const onLoad = vi.fn();
  const fetchMock = vi.fn(async () => Response.json({ ok: true, state: 'unloaded' }));
  vi.stubGlobal('fetch', fetchMock);

  const status: ImageModelStatusView = {
    ok: false,
    service: 'image',
    enabled: true,
    provider: 'flux_klein',
    model: 'FLUX.2 [klein] 4B',
    loaded: false,
    state: 'unavailable',
    error: 'image_service_unreachable',
    supports_download: true,
    downloaded: true,
    local_model: {
      complete: true,
      local_dir: 'resources/models/image/flux2-klein-4b',
    },
  };

  renderControl(status, { onRefresh, onLoad });

  expect(screen.queryByRole('button', { name: 'Download Model' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Load Model' }));

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith('/api/image-generation/service/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: 'flux_klein' }),
    });
    expect(onLoad).toHaveBeenCalledWith('flux_klein');
  });
  expect(onRefresh).not.toHaveBeenCalled();
});

it('does not poll model status while no download is active', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  const status: ImageModelStatusView = {
    ok: true,
    service: 'image',
    enabled: true,
    provider: 'flux_klein',
    model: 'FLUX.2 [klein] 4B',
    loaded: false,
    state: 'unloaded',
    downloaded: true,
    local_model: { complete: true },
  };

  renderControl(status);
  await vi.advanceTimersByTimeAsync(30_000);

  expect(fetchMock).not.toHaveBeenCalled();
});

it('shows byte and percentage progress while a model downloads', () => {
  const status: ImageModelStatusView = {
    ok: false,
    service: 'image',
    enabled: true,
    provider: 'z_image_turbo',
    model: 'Z-Image Turbo',
    loaded: false,
    state: 'downloading',
    supports_download: true,
    local_model: { complete: false },
    download_progress: {
      status: 'downloading',
      bytes_downloaded: 5 * 1024 * 1024 * 1024,
      bytes_total: 20 * 1024 * 1024 * 1024,
      percent: 25,
      indeterminate: false,
    },
  };

  renderControl(status, {
    action: { type: 'download', provider: 'z_image_turbo' },
  });

  expect(screen.getByLabelText('Model download progress bar')).toHaveAttribute('aria-valuenow', '25');
  expect(screen.getByText(/5\.00 GB of 20\.0 GB · 25\.0%/)).toBeInTheDocument();
});

it('uses model-specific generation defaults', () => {
  const base = {
    width: 768,
    height: 768,
    unloadAfterGeneration: false,
  };

  const krea = imageRequestDefaultValues({ ...base, providerId: 'image:krea2_turbo' });
  const zImage = imageRequestDefaultValues({ ...base, providerId: 'image:z_image_turbo' });
  const flux = imageRequestDefaultValues({ ...base, providerId: 'image:flux_klein' });

  expect(flux.steps).toBe('4');
  expect(flux.guidanceScale).toBe('1');
  expect(krea.steps).toBe('8');
  expect(krea.guidanceScale).toBe('0');
  expect(zImage.steps).toBe('9');
  expect(zImage.guidanceScale).toBe('0');
});

it('removes reference assets for text-to-image-only models', () => {
  const defaults = {
    providerId: 'image:krea2_turbo',
    width: 768,
    height: 768,
    unloadAfterGeneration: false,
  };
  const values = {
    ...imageRequestDefaultValues(defaults),
    prompt: 'city',
    referenceAssetIds: ['image:reference'],
  };

  const input = buildImageGenerateInput(values, defaults);
  const resolved = resolveImageRequestDefaults(defaults);

  expect(resolved.supportsImageToImage).toBe(false);
  expect(input.reference_asset_ids).toEqual([]);
  expect(input.no_cache).toBe(false);
});
