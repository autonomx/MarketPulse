import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../design/theme';
import { ImageRequestForm } from './ImageRequestForm';
import type { ImageRequestFormValues } from './imageRequestModel';
import { fixture } from '../../test/fixture';
import { createTestQueryClient } from '../../test/renderWithProviders';

function renderForm(onSubmit: (values: ImageRequestFormValues) => void) {
  const queryClient = createTestQueryClient();
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <ImageRequestForm
          defaults={{ providerId: 'image:flux_klein', width: 768, height: 768, unloadAfterGeneration: false }}
          providers={[fixture({
            id: 'image:flux_klein',
            label: 'FLUX.2 [klein] 4B',
            family: 'image',
            source: 'settings',
            status: 'configured',
            capabilities: ['image'],
          })]}
          pending={false}
          onSubmit={onSubmit}
        />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ImageRequestForm wiring', () => {
  it('submits provider, uploaded reference image, aspect ratio, style, quality, and advanced controls', async () => {
    const onSubmit = vi.fn<(values: ImageRequestFormValues) => void>();
    const fetchMock = vi.fn(async () => Response.json({
      ok: true,
      asset: {
        id: 'image-reference:upload-one',
        module: 'image-reference',
        type: 'image',
        mime_type: 'image/png',
        file_name: 'reference-one.png',
        source_job_id: null,
        created_at: '2026-07-07T00:00:00Z',
        metadata: { title: 'reference-one.png', width: 768, height: 768 },
        compat: { uploaded_reference: true },
      },
    }));
    vi.stubGlobal('fetch', fetchMock);
    renderForm(onSubmit);

    expect(screen.getByLabelText('Provider')).toHaveValue('image:flux_klein');
    expect(screen.getByLabelText('Style')).toHaveValue('photorealistic');
    expect(screen.getByLabelText('Steps')).toHaveValue(4);

    fireEvent.click(screen.getByRole('button', { name: 'Use 16:9 Widescreen' }));
    expect(screen.getByLabelText('Width')).toHaveValue(1024);
    expect(screen.getByLabelText('Height')).toHaveValue(576);

    fireEvent.click(screen.getByRole('button', { name: 'Set quality to 2 of 5' }));
    expect(screen.getByLabelText('Steps')).toHaveValue(3);
    expect(screen.getByText('3 steps')).toBeInTheDocument();

    const file = new File(['fake image'], 'reference-one.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('Select reference image from hard drive'), {
      target: { files: [file] },
    });
    expect(await screen.findByText('1 / 2 selected')).toBeInTheDocument();
    expect(screen.getByText('reference-one.png')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Keep the character and change the clothing' } });
    fireEvent.change(screen.getByLabelText('Negative prompt'), { target: { value: 'blurry' } });
    fireEvent.change(screen.getByLabelText('Guidance scale'), { target: { value: '4.5' } });
    fireEvent.click(screen.getByLabelText('Unload model after generation'));
    fireEvent.click(screen.getByLabelText('Ignore cached results'));
    fireEvent.click(screen.getByRole('button', { name: 'Generate image' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      providerId: 'image:flux_klein',
      prompt: 'Keep the character and change the clothing',
      negativePrompt: 'blurry',
      width: '1024',
      height: '576',
      style: 'photorealistic',
      referenceAssetIds: ['image-reference:upload-one'],
      steps: '3',
      guidanceScale: '4.5',
      unloadAfterGeneration: true,
      noCache: true,
    }));
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/image-generation/references?filename=reference-one.png',
      expect.objectContaining({ method: 'POST', body: file }),
    );
  });

  it('wires dimension steppers and custom aspect selection', () => {
    const onSubmit = vi.fn<(values: ImageRequestFormValues) => void>();
    renderForm(onSubmit);

    fireEvent.click(screen.getByRole('button', { name: 'Increase width' }));
    expect(screen.getByLabelText('Width')).toHaveValue(832);
    expect(screen.getByRole('button', { name: 'Use custom aspect ratio' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'Decrease height' }));
    expect(screen.getByLabelText('Height')).toHaveValue(704);
  });
});
