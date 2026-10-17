import { Button, Text } from '@mantine/core';
import { useRef, useState } from 'react';
import './ImageReferenceControl.css';
import { imageAssetTitle, imageAssetUrl, type ImageAsset } from './imageWorkspaceModel';
import type { components } from './api/generated';
import { ApiError } from '../../api/errors';
import { uploadBinary } from '../../api/transport';

export const IMAGE_REFERENCES_QUERY_KEY = ['image-generation', 'references'] as const;
const MAX_REFERENCE_IMAGES = 2;

type ImageReferenceUploadResponse = components['schemas']['ImageReferenceUploadResponse'];

interface ImageReferenceControlProps {
  selectedAssetIds: string[];
  onChange: (assetIds: string[]) => void;
}

async function uploadImageReference(file: File): Promise<ImageReferenceUploadResponse> {
  try {
    return await uploadBinary<ImageReferenceUploadResponse>('/api/image-generation/references', file, {
      query: { filename: file.name || 'reference-image' },
    });
  } catch (error) {
    if (error instanceof ApiError) throw new Error(error.body || `Reference upload failed with status ${error.status}`);
    throw error;
  }
}

export function ImageReferenceControl({ selectedAssetIds, onChange }: ImageReferenceControlProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [attachedAssets, setAttachedAssets] = useState<ImageAsset[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(false);
  const [limitMessage, setLimitMessage] = useState('');

  const removeReference = (assetId: string) => {
    setLimitMessage('');
    onChange(selectedAssetIds.filter((id) => id !== assetId));
  };

  const chooseUploads = async (files: FileList | File[] | null | undefined) => {
    setLimitMessage('');
    setUploadError(false);
    const incoming = Array.from(files ?? []).filter((file) => file.type.startsWith('image/'));
    if (!incoming.length) return;

    const slots = MAX_REFERENCE_IMAGES - selectedAssetIds.length;
    if (slots <= 0) {
      setLimitMessage(`Remove a reference before uploading another. Maximum: ${MAX_REFERENCE_IMAGES}.`);
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }

    const accepted = incoming.slice(0, slots);
    if (incoming.length > accepted.length) {
      setLimitMessage(`Attached the first ${accepted.length}; use at most ${MAX_REFERENCE_IMAGES} reference images.`);
    }

    setUploading(true);
    try {
      const uploads = await Promise.all(accepted.map(uploadImageReference));
      const uploadedAssets = uploads.map((result) => result.asset);
      setAttachedAssets((current) => [
        ...uploadedAssets,
        ...current.filter((asset) => !uploadedAssets.some((uploaded) => uploaded.id === asset.id)),
      ]);
      onChange([...selectedAssetIds, ...uploadedAssets.map((asset) => asset.id)].slice(0, MAX_REFERENCE_IMAGES));
    } catch {
      setUploadError(true);
    } finally {
      setUploading(false);
      setDragActive(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const selectedAssets = selectedAssetIds
    .map((assetId) => attachedAssets.find((asset) => asset.id === assetId))
    .filter((asset): asset is ImageAsset => Boolean(asset));

  return (
    <fieldset className="image-reference-fieldset">
      <legend>
        Reference images <i title="Condition FLUX on up to two uploaded images">i</i>
      </legend>
      <div
        className={`image-reference-dropzone ${dragActive ? 'active' : ''}`}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          setDragActive(false);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          void chooseUploads(event.dataTransfer.files);
        }}
      >
        <div>
          <strong>Drop reference images here</strong>
          <Text size="xs">PNG, JPEG, or WebP. Attach up to {MAX_REFERENCE_IMAGES} from your hard drive.</Text>
        </div>
        <Button
          loading={uploading}
          onClick={() => fileInputRef.current?.click()}
          size="compact-xs"
          type="button"
          variant="light"
        >
          Select image
        </Button>
        <input
          ref={fileInputRef}
          className="visually-hidden"
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp"
          aria-label="Select reference image from hard drive"
          onChange={(event) => void chooseUploads(event.currentTarget.files)}
        />
      </div>

      {uploadError ? <Text c="red" size="xs" role="alert">Reference upload failed.</Text> : null}
      {limitMessage ? <Text c="yellow" size="xs" role="status">{limitMessage}</Text> : null}

      {selectedAssets.length ? (
        <div role="group" className="image-reference-grid" aria-label="Attached reference images">
          {selectedAssets.map((asset) => (
            <article key={asset.id} className="image-reference-card">
              <img alt="" loading="lazy" src={imageAssetUrl(asset.id)} />
              <div>
                <span title={imageAssetTitle(asset)}>{imageAssetTitle(asset)}</span>
                <button
                  aria-label={`Remove ${imageAssetTitle(asset)} reference`}
                  onClick={() => removeReference(asset.id)}
                  type="button"
                >
                  Remove
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : null}
      <Text className="image-reference-count" size="xs">{selectedAssetIds.length} / {MAX_REFERENCE_IMAGES} selected</Text>
    </fieldset>
  );
}
