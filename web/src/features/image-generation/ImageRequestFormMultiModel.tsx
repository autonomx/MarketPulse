import { Button } from '@mantine/core';
import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import type { ProviderFacadePayload } from '../../api/client';
import { FeatureValidationMessage } from '../../shared/FeatureSubmitFeedback';
import { ImageReferenceControl } from './ImageReferenceControl';
import {
  imageRequestDefaultValues,
  resolveImageRequestDefaults,
  type ImageRequestDefaults,
  type ImageRequestFormValues,
  validateImageDimension,
} from './imageRequestModel';

interface ImageRequestFormProps {
  defaults: ImageRequestDefaults;
  providers: ProviderFacadePayload['providers'];
  lockedProviderId?: string;
  pending: boolean;
  disabled?: boolean;
  disabledReason?: string;
  resetToken?: string;
  onSubmit: (values: ImageRequestFormValues) => void;
}

const ASPECT_PRESETS = [
  { id: 'square-768', ratio: '1:1', label: 'Square', width: 768, height: 768 },
  { id: 'wide-1024', ratio: '16:9', label: 'Widescreen', width: 1024, height: 576 },
  { id: 'tall-1024', ratio: '9:16', label: 'Portrait', width: 576, height: 1024 },
  { id: 'landscape-768', ratio: '4:3', label: 'Standard', width: 1024, height: 768 },
  { id: 'portrait-768', ratio: '3:4', label: 'Portrait', width: 768, height: 1024 },
  { id: 'ultrawide-1344', ratio: '21:9', label: 'Ultrawide', width: 1344, height: 576 },
] as const;

const FALLBACK_QUALITY_STEPS = [2, 3, 4, 6, 8] as const;
const DEFAULT_QUALITY = 3;

function qualityIndex(steps: readonly number[], defaultSteps: number): number {
  const index = steps.indexOf(defaultSteps);
  return index >= 0 ? index + 1 : DEFAULT_QUALITY;
}

export function ImageRequestForm({
  defaults,
  providers,
  lockedProviderId,
  pending,
  disabled,
  disabledReason,
  resetToken,
  onSubmit,
}: ImageRequestFormProps) {
  const resolvedDefaults = useMemo(() => resolveImageRequestDefaults(defaults), [defaults]);
  const qualitySteps = useMemo(
    () => resolvedDefaults.qualitySteps?.length === 5
      ? [...resolvedDefaults.qualitySteps]
      : [...FALLBACK_QUALITY_STEPS],
    [resolvedDefaults.qualitySteps],
  );
  const defaultSteps = resolvedDefaults.steps ?? qualitySteps[DEFAULT_QUALITY - 1];
  const defaultQuality = qualityIndex(qualitySteps, defaultSteps);
  const [quality, setQuality] = useState(defaultQuality);
  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors, isDirty },
  } = useForm<ImageRequestFormValues>({ defaultValues: imageRequestDefaultValues(resolvedDefaults) });

  useEffect(() => {
    if (!isDirty) reset(imageRequestDefaultValues(resolvedDefaults));
  }, [isDirty, reset, resolvedDefaults]);

  useEffect(() => {
    setQuality(defaultQuality);
    setValue('steps', String(defaultSteps), { shouldDirty: false, shouldValidate: true });
    setValue(
      'guidanceScale',
      resolvedDefaults.guidanceScale === undefined ? '' : String(resolvedDefaults.guidanceScale),
      { shouldDirty: false, shouldValidate: true },
    );
    if (resolvedDefaults.supportsImageToImage === false) {
      setValue('referenceAssetIds', [], { shouldDirty: false });
    }
  }, [
    defaultQuality,
    defaultSteps,
    resolvedDefaults.guidanceScale,
    resolvedDefaults.providerId,
    resolvedDefaults.supportsImageToImage,
    setValue,
  ]);

  useEffect(() => {
    if (resetToken) {
      reset(imageRequestDefaultValues(resolvedDefaults));
      setQuality(defaultQuality);
    }
  }, [defaultQuality, reset, resetToken, resolvedDefaults]);

  const selectedPreset = watch('preset');
  const prompt = watch('prompt') ?? '';
  const width = watch('width');
  const height = watch('height');
  const referenceAssetIds = watch('referenceAssetIds') ?? [];
  const widthValue = Number.parseInt(width, 10);
  const heightValue = Number.parseInt(height, 10);
  const requestedPixels = Number.isFinite(widthValue) && Number.isFinite(heightValue)
    ? widthValue * heightValue
    : 0;
  const exceedsModelPixelLimit = Boolean(
    resolvedDefaults.maxPixels && requestedPixels > resolvedDefaults.maxPixels,
  );

  useEffect(() => {
    const providerId = lockedProviderId || resolvedDefaults.providerId;
    if (!providerId) return;
    if (!lockedProviderId && isDirty) return;
    if (!providers.some((provider) => provider.id === providerId)) return;
    setValue('providerId', providerId, { shouldDirty: false });
  }, [isDirty, lockedProviderId, providers, resolvedDefaults.providerId, setValue]);

  const choosePreset = (preset: (typeof ASPECT_PRESETS)[number]) => {
    setValue('preset', preset.id, { shouldDirty: true });
    setValue('width', String(preset.width), { shouldDirty: true, shouldValidate: true });
    setValue('height', String(preset.height), { shouldDirty: true, shouldValidate: true });
  };

  const adjustDimension = (field: 'width' | 'height', value: string, delta: number) => {
    const parsed = Number.parseInt(value, 10);
    const next = Math.min(4096, Math.max(128, (Number.isFinite(parsed) ? parsed : 768) + delta));
    setValue(field, String(next), { shouldDirty: true, shouldValidate: true });
    setValue('preset', 'custom', { shouldDirty: true });
  };

  const chooseQuality = (level: number) => {
    setQuality(level);
    setValue('steps', String(qualitySteps[level - 1]), { shouldDirty: true, shouldValidate: true });
  };

  const submit = handleSubmit((values) => {
    if (exceedsModelPixelLimit) return;
    onSubmit({
      ...values,
      providerId: lockedProviderId || values.providerId,
      referenceAssetIds: resolvedDefaults.supportsImageToImage === false ? [] : values.referenceAssetIds,
      steps: values.steps || String(qualitySteps[quality - 1]),
    });
  });

  const modelLimitMessage = resolvedDefaults.maxPixels
    ? `The selected model is limited to ${resolvedDefaults.maxPixels.toLocaleString()} output pixels per request.`
    : '';
  const effectiveDisabledReason = exceedsModelPixelLimit ? modelLimitMessage : disabledReason;

  return (
    <>
      <form className="image-request-form" onSubmit={submit}>
        <label className="visually-hidden">
          Size preset
          <select aria-label="Size preset" {...register('preset')}>
            <option value="custom">Custom</option>
            {ASPECT_PRESETS.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
          </select>
        </label>

        <div className="image-request-primary-row">
          <label className="image-field image-provider-field">
            <span>Provider <i title="Controlled by the selected image model">ⓘ</i></span>
            <select aria-label="Provider" disabled={Boolean(lockedProviderId)} {...register('providerId')}>
              <option value="">Configured default provider</option>
              {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}
            </select>
          </label>

          <label className="image-field">
            <span>Width <i title="Output width in pixels">ⓘ</i></span>
            <div className="image-number-control">
              <button type="button" aria-label="Decrease width" onClick={() => adjustDimension('width', width, -64)}>−</button>
              <input aria-label="Width" type="number" min="128" max="4096" step="64" aria-invalid={Boolean(errors.width)} {...register('width', { validate: validateImageDimension })} />
              <button type="button" aria-label="Increase width" onClick={() => adjustDimension('width', width, 64)}>+</button>
            </div>
          </label>

          <label className="image-field">
            <span>Height <i title="Output height in pixels">ⓘ</i></span>
            <div className="image-number-control">
              <button type="button" aria-label="Decrease height" onClick={() => adjustDimension('height', height, -64)}>−</button>
              <input aria-label="Height" type="number" min="128" max="4096" step="64" aria-invalid={Boolean(errors.height)} {...register('height', { validate: validateImageDimension })} />
              <button type="button" aria-label="Increase height" onClick={() => adjustDimension('height', height, 64)}>+</button>
            </div>
          </label>
        </div>

        <fieldset className="image-aspect-fieldset">
          <legend>Aspect ratio <i title="Choose a common output shape">ⓘ</i></legend>
          <div className="image-aspect-grid" role="group" aria-label="Aspect ratio">
            {ASPECT_PRESETS.map((preset) => (
              <button
                className={selectedPreset === preset.id ? 'active' : ''}
                key={preset.id}
                type="button"
                aria-pressed={selectedPreset === preset.id}
                aria-label={`Use ${preset.ratio} ${preset.label}`}
                onClick={() => choosePreset(preset)}
              >
                <strong>{preset.ratio}</strong>
                <small>{preset.label}</small>
              </button>
            ))}
            <button
              className={selectedPreset === 'custom' ? 'active' : ''}
              type="button"
              aria-pressed={selectedPreset === 'custom'}
              aria-label="Use custom aspect ratio"
              onClick={() => setValue('preset', 'custom', { shouldDirty: true })}
            >
              <strong>▧</strong>
              <small>Custom</small>
            </button>
          </div>
        </fieldset>

        <label className="image-field image-prompt-field">
          <span>Prompt <i title="Describe the image you want to create or how the references should change">ⓘ</i></span>
          <textarea
            aria-label="Prompt"
            rows={4}
            maxLength={2000}
            placeholder={resolvedDefaults.supportsImageToImage === false
              ? 'Describe the image you want to create...'
              : 'Describe the image you want to create, or what to preserve and change from the reference...'}
            aria-invalid={Boolean(errors.prompt)}
            {...register('prompt', { required: true })}
          />
          <small className="image-character-count">{prompt.length} / 2000</small>
        </label>

        {resolvedDefaults.supportsImageToImage === false ? (
          <p className="image-local-note" role="note">
            <span aria-hidden="true">◇</span> Reference images are unavailable for the selected text-to-image model.
          </p>
        ) : (
          <ImageReferenceControl
            selectedAssetIds={referenceAssetIds}
            onChange={(assetIds) => setValue('referenceAssetIds', assetIds, { shouldDirty: true })}
          />
        )}

        <div className="image-request-options-row">
          <label className="image-field">
            <span>Style <i title="A visual style directive sent to the provider">ⓘ</i></span>
            <select aria-label="Style" {...register('style')}>
              <option value="photorealistic">Photorealistic</option>
              <option value="cinematic">Cinematic</option>
              <option value="concept art">Concept art</option>
              <option value="digital illustration">Digital illustration</option>
              <option value="watercolor">Watercolor</option>
              <option value="anime">Anime</option>
            </select>
          </label>

          <fieldset className="image-quality-fieldset">
            <legend>Quality <i title="Higher quality adds more inference steps">ⓘ</i></legend>
            <div className="image-quality-control" role="group" aria-label="Quality">
              {[1, 2, 3, 4, 5].map((level) => (
                <button
                  type="button"
                  key={level}
                  className={level <= quality ? 'active' : ''}
                  aria-label={`Set quality to ${level} of 5`}
                  aria-pressed={quality === level}
                  onClick={() => chooseQuality(level)}
                >★</button>
              ))}
              <output className="image-quality-value" aria-live="polite">{qualitySteps[quality - 1]} steps</output>
            </div>
          </fieldset>

          <details className="image-advanced-options">
            <summary>⚙ Advanced options</summary>
            <div className="image-advanced-grid">
              <label className="image-field image-advanced-wide">Negative prompt<textarea aria-label="Negative prompt" rows={2} placeholder="Elements to avoid" {...register('negativePrompt')} /></label>
              <label className="image-field">Seed<input aria-label="Seed" type="number" min="0" {...register('seed', { min: 0 })} /></label>
              <label className="image-field">Steps<input aria-label="Steps" type="number" min="1" max="200" {...register('steps', { min: 1, max: 200 })} /></label>
              <label className="image-field">Guidance scale<input aria-label="Guidance scale" type="number" min="0" max="100" step="0.1" {...register('guidanceScale', { min: 0, max: 100 })} /></label>
              <label className="image-check-field"><input aria-label="Unload model after generation" type="checkbox" {...register('unloadAfterGeneration')} /> Unload model after generation</label>
              <label className="image-check-field"><input aria-label="Ignore cached results" type="checkbox" {...register('noCache')} /> Ignore cached results</label>
            </div>
          </details>
        </div>

        <Button
          aria-label={pending ? 'Queueing image' : 'Generate image'}
          className="image-generate-button"
          type="submit"
          disabled={pending || disabled || exceedsModelPixelLimit}
          loading={pending}
          title={effectiveDisabledReason}
        >
          <span aria-hidden="true">✦</span> {pending ? 'Queueing Image...' : 'Generate Image'}
        </Button>
        <p className="image-local-note"><span aria-hidden="true">♢</span> Uses the selected model and saves completed output to Image Assets. Reference-conditioned requests bypass the reusable result cache.</p>
      </form>
      <FeatureValidationMessage show={Boolean(errors.prompt)} message="Enter a prompt before generating an image." />
      <FeatureValidationMessage show={Boolean(errors.width || errors.height)} message="Use dimensions from 128 to 4096 in multiples of 64." />
      <FeatureValidationMessage show={exceedsModelPixelLimit} message={modelLimitMessage} />
      {disabledReason ? <div className="image-disabled-message" role="status">{disabledReason}</div> : null}
    </>
  );
}
