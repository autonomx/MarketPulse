import { Button, Text } from '@mantine/core';
import { useState } from 'react';
import { createPortal } from 'react-dom';
import { imageAssetMetadata, imageAssetTitle, imageAssetUrl, type ImageAsset } from './imageWorkspaceModel';

interface ImagePreviewDialogAssetProps {
  asset: ImageAsset;
  onClose: () => void;
}

interface ImagePreviewDialogUrlProps {
  downloadUrl?: string;
  imageUrl: string;
  metadata?: string;
  onClose: () => void;
  title: string;
}

type ImagePreviewDialogProps = ImagePreviewDialogAssetProps | ImagePreviewDialogUrlProps;

export function ImagePreviewDialog(props: ImagePreviewDialogProps) {
  const [zoom, setZoom] = useState(1);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const title = 'asset' in props ? imageAssetTitle(props.asset) : props.title;
  const imageUrl = 'asset' in props ? imageAssetUrl(props.asset.id) : props.imageUrl;
  const downloadUrl = 'asset' in props ? imageAssetUrl(props.asset.id, true) : props.downloadUrl;
  const metadata = 'asset' in props ? imageAssetMetadata(props.asset) : props.metadata;
  const { onClose } = props;

  const updateZoom = (nextZoom: number) => setZoom(Math.min(4, Math.max(1, Number(nextZoom.toFixed(2)))));

  const dialog = (
    <div className="image-preview-overlay" role="dialog" aria-modal="true" aria-label={`Enlarged ${title}`} onClick={onClose}>
      <div className="image-preview-dialog" onClick={(event) => event.stopPropagation()}>
        <header>
          <strong title={title}>{title}</strong>
          <Button aria-label="Close enlarged image" size="compact-sm" variant="default" onClick={onClose}>Close</Button>
        </header>
        <div
          className="image-preview-viewport"
          onWheel={(event) => {
            if (!event.ctrlKey) return;
            event.preventDefault();
            updateZoom(zoom + (event.deltaY < 0 ? 0.25 : -0.25));
          }}
        >
          <img
            key={imageUrl}
            className={`image-preview-rendered-image${imageLoaded ? ' loaded' : ''}`}
            src={imageUrl}
            alt={title}
            data-testid="image-preview-loader"
            decoding="async"
            loading="eager"
            onLoad={() => {
              setImageFailed(false);
              setImageLoaded(true);
            }}
            onError={() => {
              setImageLoaded(false);
              setImageFailed(true);
            }}
            style={{ transform: `scale(${zoom})` }}
          />
          {imageFailed ? (
            <p className="image-preview-load-error" role="alert">
              The image could not be displayed. Use Open to view the original file.
            </p>
          ) : !imageLoaded ? (
            <p className="image-preview-loading" role="status">Loading image...</p>
          ) : null}
        </div>
        <footer>
          <Text size="xs">{metadata}</Text>
          <div className="image-preview-controls">
            <Button aria-label="Zoom out" disabled={zoom <= 1} size="compact-sm" variant="default" onClick={() => updateZoom(zoom - 0.25)}>-</Button>
            <span role="status" aria-label="Image zoom level">{Math.round(zoom * 100)}%</span>
            <Button aria-label="Zoom in" disabled={zoom >= 4} size="compact-sm" variant="default" onClick={() => updateZoom(zoom + 0.25)}>+</Button>
            <Button aria-label="Reset zoom" disabled={zoom === 1} size="compact-sm" variant="default" onClick={() => updateZoom(1)}>Reset</Button>
            {downloadUrl ? <Button aria-label={`Download ${title}`} component="a" href={downloadUrl} download size="compact-sm" variant="default">Download</Button> : null}
            <Button aria-label={`Open ${title} in a new tab`} component="a" href={imageUrl} target="_blank" rel="noreferrer" size="compact-sm" variant="filled">Open</Button>
          </div>
        </footer>
      </div>
    </div>
  );

  // Theme surfaces use backdrop filters, which create containing blocks for
  // fixed descendants. Rendering at the document root keeps the lightbox above
  // those surfaces and prevents it from being clipped by a job-list scroller.
  return typeof document === 'undefined' ? dialog : createPortal(dialog, document.body);
}
