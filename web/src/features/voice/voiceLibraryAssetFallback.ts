import { registerFetchMiddleware } from '../../api/fetchPipeline';

type AssetRecordLike = {
  id?: unknown;
  module?: unknown;
  type?: unknown;
};

type AssetListLike = {
  assets?: unknown;
  [key: string]: unknown;
};

let removeMiddleware: (() => void) | null = null;

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  if (init?.method) return init.method.toUpperCase();
  if (typeof Request !== 'undefined' && input instanceof Request) return input.method.toUpperCase();
  return 'GET';
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function parsedUrl(rawUrl: string): URL | null {
  try {
    const base = typeof window !== 'undefined' ? window.location.href : 'http://localhost/';
    return new URL(rawUrl, base);
  } catch {
    return null;
  }
}

function isAssetListRequest(rawUrl: string): boolean {
  const url = parsedUrl(rawUrl);
  return url ? url.pathname === '/api/assets' : rawUrl.split('?', 1)[0].endsWith('/api/assets');
}

/** Whether the requested filter can include voice profiles at all. */
function filterAdmitsVoiceProfiles(query: URLSearchParams | undefined): boolean {
  const type = query?.get('type');
  const module = query?.get('module');
  return (!type || type === 'voice_profile') && (!module || module === 'voice-cloning');
}

function isVoiceProfile(asset: AssetRecordLike): boolean {
  return asset.type === 'voice_profile' || asset.module === 'voice-cloning';
}

function asAssets(value: unknown): AssetRecordLike[] {
  return Array.isArray(value) ? value.filter((item): item is AssetRecordLike => Boolean(item && typeof item === 'object')) : [];
}

/**
 * The first page carries the complete direct voice set: aggregate rows are
 * replaced in place by their direct version and missing voices are appended.
 */
function mergeAssets(baseAssets: AssetRecordLike[], voiceAssets: AssetRecordLike[]): AssetRecordLike[] {
  const directById = new Map(
    voiceAssets
      .map((asset) => [String(asset.id ?? ''), asset] as const)
      .filter(([id]) => Boolean(id)),
  );
  const seen = new Set<string>();
  const merged = baseAssets.map((asset) => {
    const id = String(asset.id ?? '');
    if (id) seen.add(id);
    return id && directById.has(id) ? directById.get(id) ?? asset : asset;
  });
  for (const asset of voiceAssets) {
    const id = String(asset.id ?? '');
    if (id && seen.has(id)) continue;
    merged.push(asset);
    if (id) seen.add(id);
  }
  return merged;
}

/**
 * Later pages drop the voices the first page already carried, so a paginated
 * listing holds each direct voice exactly once.
 */
function withoutDirectVoices(baseAssets: AssetRecordLike[], voiceAssets: AssetRecordLike[]): AssetRecordLike[] {
  const directIds = new Set(voiceAssets.map((asset) => String(asset.id ?? '')).filter(Boolean));
  return baseAssets.filter((asset) => !directIds.has(String(asset.id ?? '')));
}

function fallbackUrl(rawUrl: string): string {
  try {
    const base = typeof window !== 'undefined' ? window.location.href : 'http://localhost/';
    return new URL('/api/voice-library', new URL(rawUrl, base)).toString();
  } catch {
    return '/api/voice-library';
  }
}

function responseWithAssets(response: Response, payload: AssetListLike, assets: AssetRecordLike[]): Response {
  const headers = new Headers(response.headers);
  headers.delete('content-length');
  headers.set('content-type', 'application/json');
  headers.set('x-omnix-voice-library-merged', 'true');
  return new Response(JSON.stringify({ ...payload, assets }), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

/** Returns a function that removes it. `fetchImpl` replaces the rest of the pipeline (tests). */
export function installVoiceLibraryAssetFallback(fetchImpl?: typeof fetch): () => void {
  if (removeMiddleware || typeof window === 'undefined') return () => undefined;

  const remove = registerFetchMiddleware('voice-library-asset-fallback', async (input, init, next) => {
    const delegate = fetchImpl ?? next;
    const rawUrl = requestUrl(input);
    if (requestMethod(input, init) !== 'GET' || !isAssetListRequest(rawUrl)) {
      return delegate(input, init);
    }
    const query = parsedUrl(rawUrl)?.searchParams;
    if (!filterAdmitsVoiceProfiles(query)) {
      return delegate(input, init);
    }
    const isFirstPage = !query?.get('cursor');

    const response = await delegate(input, init);
    if (!response.ok) return response;

    let payload: AssetListLike;
    try {
      payload = await response.clone().json() as AssetListLike;
    } catch (error) {
      console.warn('[Voice Library][fallback] aggregate asset response was not JSON', error);
      return response;
    }

    const baseAssets = asAssets(payload.assets);
    const directUrl = fallbackUrl(rawUrl);
    try {
      const directResponse = await delegate(directUrl, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!directResponse.ok) {
        console.error('[Voice Library][fallback] direct endpoint failed', {
          requestUrl: directUrl,
          status: directResponse.status,
          statusText: directResponse.statusText,
          responseBody: (await directResponse.text()).slice(0, 2_000),
        });
        return response;
      }

      const directPayload = await directResponse.json() as AssetListLike;
      const voiceAssets = asAssets(directPayload.assets).filter(isVoiceProfile);
      if (!voiceAssets.length) {
        console.warn('[Voice Library][fallback] direct endpoint returned no voice profiles', {
          requestUrl: directUrl,
          aggregateAssetCount: baseAssets.length,
          directAssetCount: asAssets(directPayload.assets).length,
        });
        return response;
      }

      const mergedAssets = isFirstPage ? mergeAssets(baseAssets, voiceAssets) : withoutDirectVoices(baseAssets, voiceAssets);
      console.info('[Voice Library][fallback] merged authoritative direct voice profiles', {
        requestUrl: directUrl,
        isFirstPage,
        aggregateAssetCount: baseAssets.length,
        aggregateVoiceProfileCount: baseAssets.filter(isVoiceProfile).length,
        directVoiceProfileCount: voiceAssets.length,
        mergedAssetCount: mergedAssets.length,
      });
      return responseWithAssets(response, payload, mergedAssets);
    } catch (error) {
      console.error('[Voice Library][fallback] direct endpoint request threw', {
        requestUrl: directUrl,
        error,
      });
      return response;
    }
  });
  removeMiddleware = remove;
  return () => {
    remove();
    if (removeMiddleware === remove) removeMiddleware = null;
  };
}

export function resetVoiceLibraryAssetFallbackForTests(): void {
  removeMiddleware?.();
  removeMiddleware = null;
}
