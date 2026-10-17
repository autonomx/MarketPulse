import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldBundleTransfer } from './RpgWorldBundleTransfer';

function requestUrl(input: RequestInfo | URL): URL {
  return new URL(typeof input === 'string' ? input : input.toString(), 'http://localhost');
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('RpgWorldBundleTransfer', () => {
  it('downloads an exported world bundle', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const pathname = requestUrl(input).pathname;
      if (pathname === '/api/rpg/world-library') {
        return Response.json({
          ok: true,
          worlds: [{ id: 'world:portable', title: 'Portable World' }],
          scenarios: [],
          campaigns: [],
          generation_runs: [],
        });
      }
      expect(pathname).toBe('/api/rpg/worlds/world%3Aportable/export');
      return new Response(new Blob(['portable']), {
        headers: {
          'content-type': 'application/zip',
          'content-disposition': 'attachment; filename="portable.omnix-world.zip"',
        },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:portable');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    render(<RpgWorldBundleTransfer initialWorldId="world:portable" />);
    expect(await screen.findByRole('option', { name: 'Portable World (world:portable)' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Export world bundle' }));

    expect(await screen.findByText('World bundle exported: portable.omnix-world.zip')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('imports a bundle under an optional clone world id', async () => {
    const onImported = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url.pathname === '/api/rpg/world-library') {
        return Response.json({ ok: true, worlds: [], scenarios: [], campaigns: [], generation_runs: [] });
      }
      expect(url.pathname).toBe('/api/rpg/worlds/import');
      expect(url.searchParams.get('target_world_id')).toBe('world:clone');
      expect(init?.method).toBe('POST');
      expect(init?.body).toBeInstanceOf(File);
      return Response.json({
        ok: true,
        status: 'imported',
        world_id: 'world:clone',
        source_world_id: 'world:source',
        bundle_sha256: 'a'.repeat(64),
        counts: { map_definitions: 2, images_created: 1, images_reused: 1 },
        identifier_map: {},
        warnings: [],
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<RpgWorldBundleTransfer onImported={onImported} />);

    const file = new File(['bundle'], 'source.omnix-world.zip', { type: 'application/zip' });
    fireEvent.change(screen.getByLabelText('World bundle file'), {
      target: { files: [file] },
    });
    fireEvent.change(screen.getByLabelText('New world id (optional)'), {
      target: { value: 'world:clone' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import world bundle' }));

    expect(await screen.findByText('World imported: world:clone • 2 maps • 2 images')).toBeInTheDocument();
    await waitFor(() => expect(onImported).toHaveBeenCalledWith('world:clone'));
  });

  it('reports automatic launch preparation for an authoring-only import', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.pathname === '/api/rpg/world-library') {
        return Response.json({ ok: true, worlds: [], scenarios: [], campaigns: [], generation_runs: [] });
      }
      return Response.json({
        ok: true,
        status: 'imported',
        world_id: 'world:authoring',
        source_world_id: 'world:source',
        bundle_sha256: 'a'.repeat(64),
        counts: {},
        identifier_map: {},
        warnings: [],
        launch_preparation: { status: 'generating' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<RpgWorldBundleTransfer />);

    const file = new File(['bundle'], 'authoring.omnix-world.zip', { type: 'application/zip' });
    fireEvent.change(screen.getByLabelText('World bundle file'), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import world bundle' }));

    expect(await screen.findByText('World imported: world:authoring. Launch preparation has started.')).toBeInTheDocument();
  });
});
