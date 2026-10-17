import { Button, Text, Title } from '@mantine/core';
import { useState } from 'react';
import {
  formatCreatedAt,
  imageAssetMetadata,
  imageAssetTitle,
  imageAssetUrl,
  type ImageAsset,
} from './imageWorkspaceModel';
import { ImagePreviewDialog } from './ImagePreviewDialog';

interface ImageLatestResultProps {
  asset?: ImageAsset;
  onOpenInAssets?: (assetId: string) => void;
}

export function ImageLatestResult({ asset, onOpenInAssets }: ImageLatestResultProps) {
  const [expanded, setExpanded] = useState(false);
  const title = asset ? imageAssetTitle(asset) : '';
  const imageUrl = asset ? imageAssetUrl(asset.id) : '';

  const closeExpanded = () => setExpanded(false);

  return (
    <>
      <section
        className={`image-surface image-latest-card ${asset ? 'has-result' : ''}`}
        aria-atomic="true"
        aria-label="Latest result"
        aria-live="polite"
      >
        <header className="image-section-header image-section-header-compact">
          <div className="image-section-heading">
            <span className="image-section-icon" aria-hidden="true">✦</span>
            <div>
              <Title id="latest-image-result-title" order={3}>Latest Result</Title>
              <Text size="sm">Your most recently generated image appears here first.</Text>
            </div>
          </div>
        </header>

        {asset ? (
          <div className="image-latest-content">
            <button
              type="button"
              className="image-latest-preview-button"
              aria-label={`Enlarge ${title}`}
              onClick={() => setExpanded(true)}
            >
              <img src={imageUrl} alt={title} decoding="async" />
            </button>
            <div className="image-latest-details">
              <strong title={title}>{title}</strong>
              <Text size="xs">{imageAssetMetadata(asset)}</Text>
              <Text size="xs">{formatCreatedAt(asset.created_at)}</Text>
              <span className="image-completed-label">● Completed</span>
              <div className="image-latest-actions">
                {onOpenInAssets ? (
                  <Button aria-label="Open in Assets" size="compact-sm" variant="filled" onClick={() => onOpenInAssets(asset.id)}>▣ Open in Assets</Button>
                ) : (
                  <Button aria-label="Open in Assets" component="a" href="#image-assets" size="compact-sm" variant="filled">▣ Open in Assets</Button>
                )}
                <Button aria-label={`Download ${title}`} component="a" href={imageAssetUrl(asset.id, true)} download size="compact-sm" variant="default">↓</Button>
                <Button aria-label={`Open ${title} in a new tab`} component="a" href={imageUrl} target="_blank" rel="noreferrer" size="compact-sm" variant="default">↗</Button>
              </div>
            </div>
          </div>
        ) : (
          <div className="image-empty-state" role="status">
            <span aria-hidden="true">✦</span>
            <strong>No result yet</strong>
            <small>Generate an image to see the latest result here.</small>
          </div>
        )}
      </section>

      {asset && expanded ? <ImagePreviewDialog asset={asset} onClose={closeExpanded} /> : null}
    </>
  );
}
