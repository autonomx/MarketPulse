export const IMAGE_SIZE_PRESETS = [
  { id: 'square-768', label: 'Square 768 x 768', width: 768, height: 768 },
  { id: 'portrait-768', label: 'Portrait 768 x 1024', width: 768, height: 1024 },
  { id: 'landscape-768', label: 'Landscape 1024 x 768', width: 1024, height: 768 },
  { id: 'square-1024', label: 'Square HD 1024 x 1024', width: 1024, height: 1024 },
] as const;

export interface ImageRequestDefaults {
  providerId: string;
  width: number;
  height: number;
  unloadAfterGeneration: boolean;
  steps?: number;
  guidanceScale?: number;
  supportsImageToImage?: boolean;
  maxPixels?: number;
  qualitySteps?: readonly number[];
}

const MODEL_REQUEST_POLICIES: Record<
  string,
  Pick<
    ImageRequestDefaults,
    'steps' | 'guidanceScale' | 'supportsImageToImage' | 'maxPixels' | 'qualitySteps'
  >
> = {
  flux_klein: {
    steps: 4,
    guidanceScale: 1,
    supportsImageToImage: true,
    maxPixels: 1024 * 1024,
    qualitySteps: [2, 3, 4, 6, 8],
  },
  krea2_turbo: {
    steps: 8,
    guidanceScale: 0,
    supportsImageToImage: false,
    maxPixels: 1024 * 1024,
    qualitySteps: [4, 6, 8, 10, 12],
  },
  z_image_turbo: {
    steps: 9,
    guidanceScale: 0,
    supportsImageToImage: false,
    maxPixels: 1024 * 1024,
    qualitySteps: [5, 7, 9, 12, 16],
  },
};

export function resolveImageRequestDefaults(defaults: ImageRequestDefaults): ImageRequestDefaults {
  const providerKey = defaults.providerId.replace(/^image:/, '').trim();
  const policy = MODEL_REQUEST_POLICIES[providerKey] ?? MODEL_REQUEST_POLICIES.flux_klein;
  return {
    ...defaults,
    steps: defaults.steps ?? policy.steps,
    guidanceScale: defaults.guidanceScale ?? policy.guidanceScale,
    supportsImageToImage: defaults.supportsImageToImage ?? policy.supportsImageToImage,
    maxPixels: defaults.maxPixels ?? policy.maxPixels,
    qualitySteps: defaults.qualitySteps ?? policy.qualitySteps,
  };
}

export interface ImageRequestFormValues {
  providerId: string;
  prompt: string;
  negativePrompt: string;
  preset: string;
  width: string;
  height: string;
  style: string;
  referenceAssetIds: string[];
  seed: string;
  steps: string;
  guidanceScale: string;
  unloadAfterGeneration: boolean;
  noCache: boolean;
}

export function imageRequestDefaultValues(defaults: ImageRequestDefaults): ImageRequestFormValues {
  const resolved = resolveImageRequestDefaults(defaults);
  return {
    providerId: resolved.providerId,
    prompt: '',
    negativePrompt: '',
    preset: matchingImagePreset(resolved.width, resolved.height)?.id ?? 'custom',
    width: String(resolved.width),
    height: String(resolved.height),
    style: 'photorealistic',
    referenceAssetIds: [],
    seed: '',
    steps: String(resolved.steps ?? 4),
    guidanceScale: resolved.guidanceScale === undefined ? '' : String(resolved.guidanceScale),
    unloadAfterGeneration: resolved.unloadAfterGeneration,
    noCache: false,
  };
}

export function buildImageGenerateInput(values: ImageRequestFormValues, defaults: ImageRequestDefaults) {
  const resolved = resolveImageRequestDefaults(defaults);
  return {
    prompt: values.prompt.trim(),
    negative_prompt: values.negativePrompt.trim(),
    provider_id: values.providerId.trim() || null,
    width: parseDimension(values.width, resolved.width),
    height: parseDimension(values.height, resolved.height),
    style: values.style.trim(),
    reference_asset_ids: resolved.supportsImageToImage === false ? [] : values.referenceAssetIds,
    seed: optionalInteger(values.seed),
    steps: optionalInteger(values.steps),
    guidance_scale: optionalNumber(values.guidanceScale),
    unload_after_generation: values.unloadAfterGeneration,
    no_cache: values.noCache || (resolved.supportsImageToImage !== false && values.referenceAssetIds.length > 0),
  };
}

export function matchingImagePreset(width: number, height: number) {
  return IMAGE_SIZE_PRESETS.find((preset) => preset.width === width && preset.height === height);
}

export function imagePresetById(presetId: string) {
  return IMAGE_SIZE_PRESETS.find((preset) => preset.id === presetId);
}

export function validateImageDimension(value: string): true | string {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 128 || parsed > 4096) return 'Use a value from 128 to 4096.';
  if (parsed % 64 !== 0) return 'Use a multiple of 64.';
  return true;
}

function parseDimension(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function optionalInteger(value: string): number | undefined {
  const normalized = value.trim();
  if (!normalized) return undefined;
  const parsed = Number.parseInt(normalized, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function optionalNumber(value: string): number | undefined {
  const normalized = value.trim();
  if (!normalized) return undefined;
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) ? parsed : undefined;
}
