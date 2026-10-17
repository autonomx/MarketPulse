import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import {
  applyCharacterAvatarPackToTrackedRuntimes,
  readLatestTrustedCharacterRuntime,
  type CharacterProfile,
} from './characterClient';
import {
  characterAvatarAssetUrl,
  characterAvatarClient,
  type Live2DModelCatalogItem,
} from './characterAvatarClient';
import { Live2DModelThumbnail } from './Live2DModelThumbnail';
import { forceRenderLive2DAvatar } from './live2dCharacterRenderer';
import './CharacterAvatarPanel.css';

const ACTIVE_GENERATION_STATES = new Set(['queued', 'generating_base', 'generating_variants']);
const VISEME_KEYS = ['A', 'E', 'O', 'U', 'MBP', 'FV', 'L', 'WQ', 'other'] as const;
const AVATAR_UPLOAD_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const MAX_AVATAR_UPLOAD_BYTES = 12 * 1024 * 1024;

type AvatarEditorMode = 'generated' | 'live2d';

export function CharacterAvatarPanel({ character }: { character: CharacterProfile }) {
  const queryClient = useQueryClient();
  const [editorMode, setEditorMode] = useState<AvatarEditorMode>('generated');
  const [appearancePrompt, setAppearancePrompt] = useState('');
  const [style, setStyle] = useState('illustrated character portrait');
  const [outfitPrompt, setOutfitPrompt] = useState('');
  const [backgroundPrompt, setBackgroundPrompt] = useState('');
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [sourcePreviewUrl, setSourcePreviewUrl] = useState<string | null>(null);
  const [sourceConsentConfirmed, setSourceConsentConfirmed] = useState(false);
  const [generationId, setGenerationId] = useState<string | null>(null);
  const [visemeGenerationId, setVisemeGenerationId] = useState<string | null>(null);
  const [visemeRequestedForGenerationId, setVisemeRequestedForGenerationId] = useState<string | null>(null);
  const [selectedLive2DModelId, setSelectedLive2DModelId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const packQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-avatar-pack', character.id],
    queryFn: () => characterAvatarClient.optionalPack(character.id),
    retry: false,
  });
  const live2dCatalogQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-live2d-models', character.id],
    queryFn: () => characterAvatarClient.live2dCatalog(character.id),
    retry: false,
  });
  const generationQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-avatar-generation', generationId],
    queryFn: () => characterAvatarClient.generation(generationId ?? ''),
    enabled: Boolean(generationId),
    refetchInterval: (query) => ACTIVE_GENERATION_STATES.has(query.state.data?.status ?? '') ? 1_500 : false,
  });
  const visemeQuery = useQuery({
    queryKey: ['feature', 'chatbot', 'character-avatar-visemes', visemeGenerationId],
    queryFn: () => characterAvatarClient.visemeGeneration(visemeGenerationId ?? ''),
    enabled: Boolean(visemeGenerationId),
    refetchInterval: (query) => query.state.data?.status === 'generating' ? 1_500 : false,
  });

  const visemeMutation = useMutation({
    mutationFn: () => characterAvatarClient.createVisemeGeneration(character.id),
    onSuccess: (batch) => {
      setVisemeGenerationId(batch.id);
      setStatus('Expanded viseme generation queued. Audio-envelope lip sync remains active until it completes.');
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Viseme generation could not be queued.'),
  });
  const activateLive2DMutation = useMutation({
    mutationFn: (modelId: string) => characterAvatarClient.activateLive2d(character.id, {
      model_id: modelId,
      // The selection UI no longer blocks on separate checkbox controls. The
      // API still receives explicit acknowledgement fields for its safety
      // contract and audit trail.
      accept_live2d_runtime_terms: true,
      accept_model_terms: true,
    }),
    onSuccess: (result) => {
      applyCharacterAvatarPackToTrackedRuntimes(character.id, result.avatar_pack ?? null);
      const liveRuntime = readLatestTrustedCharacterRuntime();
      const live2dActivated = result.avatar_pack?.renderer === 'live2d';
      if (live2dActivated) {
        // Use the mutation response, not the mutable runtime cache. A request
        // that started before selection may still hold the previous pack.
        if (liveRuntime?.character_id === character.id) {
          forceRenderLive2DAvatar({ ...liveRuntime, avatar_pack: result.avatar_pack });
        }
      }
      setStatus(result.downloaded
        ? 'Live2D runtime and model downloaded. Live Voice is now using the new idle avatar.'
        : 'Live2D avatar selected. Live Voice is now using the new idle avatar.');
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-avatar-pack', character.id] });
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-live2d-models', character.id] });
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'live-call-runtime'] });
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Live2D avatar could not be activated.'),
  });
  const disableLive2DMutation = useMutation({
    mutationFn: () => characterAvatarClient.disableLive2d(character.id),
    onSuccess: (result) => {
      applyCharacterAvatarPackToTrackedRuntimes(character.id, result.avatar_pack ?? null);
      setEditorMode('generated');
      setStatus(result.avatar_pack
        ? 'The previous generated avatar has been restored.'
        : 'Live2D has been disabled. Live calls will use the voice orb until another avatar is selected.');
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-avatar-pack', character.id] });
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-live2d-models', character.id] });
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'live-call-runtime'] });
    },
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Live2D avatar could not be disabled.'),
  });

  useEffect(() => {
    setEditorMode('generated');
    setSourceFile(null);
    setSourceConsentConfirmed(false);
    setGenerationId(null);
    setVisemeGenerationId(null);
    setVisemeRequestedForGenerationId(null);
    setSelectedLive2DModelId(null);
    setStatus(null);
  }, [character.id]);

  useEffect(() => {
    if (packQuery.data?.renderer === 'live2d') setEditorMode('live2d');
  }, [packQuery.data?.renderer]);

  useEffect(() => {
    const models = live2dCatalogQuery.data?.models;
    if (!models?.length) return;

    setSelectedLive2DModelId((currentModelId) => {
      // Keep an intentional card selection when catalog data refreshes. Previously
      // this effect always restored the active server model after a click, making
      // every other model appear impossible to select.
      if (currentModelId && models.some((model) => model.id === currentModelId)) {
        return currentModelId;
      }
      return models.find((model) => model.selected)?.id ?? models[0].id;
    });
  }, [live2dCatalogQuery.data]);

  useEffect(() => {
    if (!sourceFile || typeof URL.createObjectURL !== 'function') {
      setSourcePreviewUrl(null);
      return undefined;
    }
    const previewUrl = URL.createObjectURL(sourceFile);
    setSourcePreviewUrl(previewUrl);
    return () => URL.revokeObjectURL(previewUrl);
  }, [sourceFile]);

  useEffect(() => {
    const batch = generationQuery.data;
    if (!batch) return;
    if (batch.status === 'completed') {
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-avatar-pack', character.id] });
      setStatus('Avatar presentation pack is ready. Precise visemes are continuing in the background.');
    } else if (batch.status === 'failed') {
      setStatus(batch.error || 'Avatar generation failed.');
    }
  }, [character.id, generationQuery.data, queryClient, visemeMutation, visemeRequestedForGenerationId]);

  useEffect(() => {
    const batch = visemeQuery.data;
    if (!batch) return;
    if (batch.status === 'completed') {
      setStatus(`Precise viseme avatar pack v${batch.avatar_pack_version ?? 1} is ready.`);
      void queryClient.invalidateQueries({ queryKey: ['feature', 'chatbot', 'character-avatar-pack', character.id] });
    } else if (batch.status === 'failed') {
      setStatus(`${batch.error || 'Viseme generation failed.'} Audio-envelope lip sync remains available.`);
    }
  }, [character.id, queryClient, visemeQuery.data]);

  function acceptGeneration(batchId: string, message: string): void {
    setGenerationId(batchId);
    setVisemeGenerationId(null);
    setVisemeRequestedForGenerationId(null);
    setStatus(message);
  }

  function generationOptions() {
    return {
      appearance_prompt: appearancePrompt,
      style,
      outfit_prompt: outfitPrompt,
      background_prompt: backgroundPrompt,
      include_blink: true,
      include_expressions: true,
      include_outfit: Boolean(outfitPrompt.trim()),
      include_background: Boolean(backgroundPrompt.trim()),
    };
  }

  const generateMutation = useMutation({
    mutationFn: () => characterAvatarClient.createGeneration(character.id, generationOptions()),
    onSuccess: (batch) => acceptGeneration(
      batch.id,
      'Canonical portrait generation queued. Presentation and precise viseme frames follow automatically.',
    ),
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Avatar generation could not be queued.'),
  });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!sourceFile) throw new Error('Choose a JPEG, PNG, or WebP image first.');
      if (!AVATAR_UPLOAD_TYPES.has(sourceFile.type)) throw new Error('Use a JPEG, PNG, or WebP image.');
      if (sourceFile.size > MAX_AVATAR_UPLOAD_BYTES) throw new Error('Avatar source images must be 12 MB or smaller.');
      if (!sourceConsentConfirmed) throw new Error('Confirm that you own the image or have permission to use it.');
      const sourceAsset = await characterAvatarClient.uploadSourceImage(sourceFile);
      return characterAvatarClient.createGeneration(character.id, {
        ...generationOptions(),
        source_asset_id: sourceAsset.id,
        source_image_consent_confirmed: true,
      });
    },
    onSuccess: (batch) => acceptGeneration(
      batch.id,
      'Uploaded image accepted. A normalized closed-mouth portrait, presentation frames, and precise visemes will be generated automatically.',
    ),
    onError: (error) => setStatus(error instanceof Error ? error.message : 'Avatar source image could not be uploaded.'),
  });

  const pack = packQuery.data;
  const isLive2D = pack?.renderer === 'live2d';
  const previewAssetId = isLive2D ? '' : pack?.mouth_frames.closed || pack?.mouth_frames.silence || pack?.base_asset_id || '';
  const selectedLive2DModel = live2dCatalogQuery.data?.models.find((model) => model.id === selectedLive2DModelId) ?? null;
  const activeLive2DModel = live2dCatalogQuery.data?.models.find((model) => model.selected) ?? null;
  const generation = generationQuery.data;
  const visemeGeneration = visemeQuery.data;
  const generatedVariants = useMemo(() => Object.keys(generation?.asset_ids ?? {}).length, [generation?.asset_ids]);
  const generatedVisemes = useMemo(() => Object.keys(visemeGeneration?.asset_ids ?? {}).length, [visemeGeneration?.asset_ids]);
  const generationActive = ACTIVE_GENERATION_STATES.has(generation?.status ?? '');
  const visemeActive = visemeMutation.isPending || visemeGeneration?.status === 'generating';
  const anyGenerationActive = generateMutation.isPending || uploadMutation.isPending || generationActive || visemeActive;
  const live2dBusy = activateLive2DMutation.isPending || disableLive2DMutation.isPending;

  return <section className="character-dashboard-section character-avatar-panel" aria-labelledby={`character-avatar-${character.id}`}>
    <header className="character-section-heading">
      <div><span>03</span><h4 id={`character-avatar-${character.id}`}>Live avatar</h4></div>
    </header>

    <div className="character-avatar-type-tabs" role="tablist" aria-label="Avatar type">
      <button
        type="button"
        role="tab"
        aria-selected={editorMode === 'generated'}
        className={editorMode === 'generated' ? 'is-active' : undefined}
        onClick={() => setEditorMode('generated')}
      >Generated avatar</button>
      <button
        type="button"
        role="tab"
        aria-selected={editorMode === 'live2d'}
        className={editorMode === 'live2d' ? 'is-active' : undefined}
        onClick={() => setEditorMode('live2d')}
      >Live2D avatar</button>
    </div>

    <div className="character-avatar-layout">
      <div className="character-avatar-preview">
        {previewAssetId ? <img src={characterAvatarAssetUrl(previewAssetId)} alt={`${character.display_name} avatar preview`} /> : isLive2D ? <Live2DPreview model={activeLive2DModel} /> : <div className="character-avatar-empty"><span aria-hidden="true">◌</span><strong>No avatar pack</strong><small>Generate one, upload an image, or select Live2D.</small></div>}
        {pack ? <div className="character-avatar-meta"><span><small>Pack version</small><strong>v{pack.version}</strong></span><span><small>Render mode</small><strong>{pack.render_mode.replace('_', ' ')}</strong></span><span><small>Renderer</small><strong>{pack.renderer}</strong></span></div> : null}
      </div>

      {editorMode === 'generated' ? <div className="character-avatar-form">
        {isLive2D ? <div className="character-avatar-mode-notice"><strong>Live2D is currently active.</strong><span>Generating a new image pack will replace it. You can also restore the previously generated pack from the Live2D tab.</span></div> : null}
        <div className="character-avatar-upload-card">
          <div className="character-avatar-upload-preview">
            {sourcePreviewUrl ? <img src={sourcePreviewUrl} alt="Selected avatar source preview" /> : <span aria-hidden="true">＋</span>}
          </div>
          <div className="character-avatar-upload-copy">
            <strong>Use your own image</strong>
            <p>Upload a clear front-facing photo or portrait. Omnix stores it as a shared reference asset, then the configured Image Generation provider creates the canonical closed-mouth portrait, lip frames, expressions, and visemes.</p>
            <div className="character-avatar-upload-actions">
              <label className="character-avatar-upload-picker">
                <span>{sourceFile ? 'Replace image' : 'Choose image'}</span>
                <input
                  aria-label="Upload source image"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(event) => {
                    setSourceFile(event.currentTarget.files?.[0] ?? null);
                    setSourceConsentConfirmed(false);
                  }}
                />
              </label>
              {sourceFile ? <small>{sourceFile.name} · {Math.max(1, Math.round(sourceFile.size / 1024))} KB</small> : <small>JPEG, PNG, or WebP · maximum 12 MB</small>}
            </div>
            <label className="character-avatar-upload-consent">
              <input
                aria-label="Confirm avatar source image rights"
                type="checkbox"
                checked={sourceConsentConfirmed}
                onChange={(event) => setSourceConsentConfirmed(event.currentTarget.checked)}
              />
              <span>I own this image or have permission from the depicted person to use it for avatar generation.</span>
            </label>
            <button
              type="button"
              disabled={!sourceFile || !sourceConsentConfirmed || anyGenerationActive}
              onClick={() => uploadMutation.mutate()}
            >
              {uploadMutation.isPending ? 'Uploading image…' : generationActive ? 'Generating avatar pack…' : 'Upload image and generate avatar pack'}
            </button>
          </div>
        </div>

        <label>Appearance direction<textarea rows={3} value={appearancePrompt} placeholder="Optional styling, clothing, visual mood, and framing guidance…" onChange={(event) => setAppearancePrompt(event.currentTarget.value)} /></label>
        <label>Visual style<input value={style} onChange={(event) => setStyle(event.currentTarget.value)} /></label>
        <div className="character-avatar-form-grid">
          <label>Alternate outfit<input value={outfitPrompt} placeholder="Optional outfit direction" onChange={(event) => setOutfitPrompt(event.currentTarget.value)} /></label>
          <label>Alternate background<input value={backgroundPrompt} placeholder="Optional room or scene" onChange={(event) => setBackgroundPrompt(event.currentTarget.value)} /></label>
        </div>
        <div className="character-avatar-actions">
          <button type="button" disabled={anyGenerationActive} onClick={() => generateMutation.mutate()}>
            {generateMutation.isPending ? 'Queueing…' : generationActive ? 'Generating avatar pack…' : pack && !isLive2D ? 'Regenerate avatar pack' : 'Generate avatar pack'}
          </button>
          {pack && !isLive2D ? <button type="button" disabled={anyGenerationActive} onClick={() => visemeMutation.mutate()}>
            {visemeActive ? 'Generating visemes…' : pack.render_mode === 'viseme' ? 'Regenerate precise visemes' : 'Generate precise visemes'}
          </button> : null}
        </div>
        {generation ? <div className="character-avatar-progress"><strong>{generation.status.replaceAll('_', ' ')}</strong><span>{generatedVariants} base and presentation assets ready</span>{generation.error ? <small>{generation.error}</small> : null}</div> : null}
        {visemeGeneration ? <div className="character-avatar-progress"><strong>{visemeGeneration.status.replaceAll('_', ' ')}</strong><span>{generatedVisemes} precise mouth shapes ready</span>{visemeGeneration.error ? <small>{visemeGeneration.error}</small> : null}</div> : null}
      </div> : <div className="character-live2d-panel">
        <div className="character-live2d-intro">
          <div><strong>Rigged Live2D avatars</strong><p>Select a model for character live calls. Omnix downloads the pinned runtime and model only after you accept their separate licenses, then serves everything locally.</p></div>
          <span>{live2dCatalogQuery.data?.runtime_installed ? 'Runtime installed' : 'Download on selection'}</span>
        </div>

        {live2dCatalogQuery.isLoading ? <div className="character-live2d-loading">Loading Live2D catalog…</div> : null}
        {live2dCatalogQuery.isError ? <div className="character-live2d-loading is-error">Live2D catalog could not be loaded.</div> : null}
        <div
          className="character-live2d-model-grid"
          role="region"
          aria-label="Live2D avatar catalog"
          tabIndex={0}
        >
          {live2dCatalogQuery.data?.models.map((model) => <button
            type="button"
            key={model.id}
            disabled={live2dBusy}
            className={`character-live2d-model-card${selectedLive2DModelId === model.id ? ' is-selected' : ''}${model.selected ? ' is-active' : ''}`}
            onClick={() => {
              setSelectedLive2DModelId(model.id);
            }}
          >
            <span className="character-live2d-model-visual"><Live2DModelThumbnail model={model} /><i>Live2D</i></span>
            <span className="character-live2d-model-copy"><strong>{model.name}</strong><small>{model.description}</small><em>{model.selected ? 'Active' : model.installed ? 'Installed' : 'Not downloaded'}</em></span>
          </button>)}
        </div>

        {selectedLive2DModel ? <div className="character-live2d-license-card">
          <strong>{selectedLive2DModel.name}</strong>
          <p>{selectedLive2DModel.license_summary}</p>
          <small className="character-live2d-license-links"><a href={selectedLive2DModel.runtime_license_url} target="_blank" rel="noreferrer">Live2D Cubism runtime license</a> · <a href={selectedLive2DModel.model_license_url} target="_blank" rel="noreferrer">sample model terms</a></small>
          <div className="character-avatar-actions">
            <button
              type="button"
              disabled={live2dBusy || selectedLive2DModel.selected}
              onClick={() => activateLive2DMutation.mutate(selectedLive2DModel.id)}
            >
              {activateLive2DMutation.isPending ? 'Downloading and activating…' : selectedLive2DModel.selected ? 'Live2D avatar active' : selectedLive2DModel.installed ? 'Use this Live2D avatar' : 'Download and use Live2D avatar'}
            </button>
            {isLive2D ? <button type="button" disabled={live2dBusy} onClick={() => disableLive2DMutation.mutate()}>
              {disableLive2DMutation.isPending ? 'Restoring…' : 'Restore previous generated avatar'}
            </button> : null}
          </div>
          {activateLive2DMutation.isPending ? <div className="character-live2d-download-progress" role="status" aria-live="polite">
            <div
              className="character-live2d-download-progress-bar"
              role="progressbar"
              aria-label="Live2D download progress"
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span />
            </div>
            <span>{selectedLive2DModel.installed ? 'Activating Live2D avatar…' : 'Downloading runtime and model files…'}</span>
          </div> : null}
        </div> : null}
      </div>}
    </div>

    {isLive2D ? <div className="character-live2d-sync-panel"><header><strong>Live2D lip sync</strong><span>Rig parameters active</span></header><p>Omnix maps the same timed TTS visemes used by generated avatar packs to the model’s mouth-open and mouth-form parameters. Physics, eye movement, and expressions remain model-driven while the live view stays idle.</p></div> : <div className="character-viseme-panel">
      <header><strong>Viseme support (9 mouth shapes)</strong><span>{pack?.render_mode === 'viseme' ? 'Precise lip sync ready' : 'Audio-envelope fallback active'}</span></header>
      <div className="character-viseme-strip">
        {VISEME_KEYS.map((viseme) => {
          const assetId = pack?.mouth_frames[viseme] || '';
          return <span className={assetId ? 'ready' : undefined} key={viseme}>
            {assetId ? <img src={characterAvatarAssetUrl(assetId)} alt={`${viseme} mouth shape`} /> : <i aria-hidden="true" />}
            <small>{viseme === 'other' ? 'Other' : viseme}</small>
          </span>;
        })}
      </div>
      <p>Used for low-latency lip sync in Omnix Chat live calls. Missing shapes fall back to the four-frame audio envelope.</p>
    </div>}

    {status ? <p className="character-avatar-status" role="status">{status}</p> : null}
  </section>;
}

function Live2DPreview({ model }: { model: Live2DModelCatalogItem | null }) {
  return <div className="character-live2d-preview">
    {model ? <Live2DModelThumbnail model={model} /> : null}
    <span>Live2D</span>
    <strong>{model?.name ?? 'Rigged avatar'}</strong>
    <small>Rendered during character live calls</small>
  </div>;
}
