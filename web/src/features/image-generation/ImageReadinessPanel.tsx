import { Button } from '@mantine/core';
import type { ImageReadiness } from './imageReadinessModel';

interface ImageReadinessPanelProps {
  readiness: ImageReadiness;
  refreshing: boolean;
  onRefresh: () => void;
}

export function ImageReadinessPanel({ readiness, refreshing, onRefresh }: ImageReadinessPanelProps) {
  return (
    <div className="image-readiness-inline" role="status" aria-live="polite">
      <span className={`image-readiness-dot ${readiness.canGenerate ? 'ready' : 'degraded'}`} aria-hidden="true" />
      <strong>{readiness.title}</strong>
      <span>{readiness.providerCount} provider{readiness.providerCount === 1 ? '' : 's'} ready</span>
      <div className="image-readiness-actions">
        <Button component="a" href="/settings" size="compact-xs" variant="subtle">Settings</Button>
        <Button component="a" href="/diagnostics" size="compact-xs" variant="subtle">Diagnostics</Button>
        <Button loading={refreshing} onClick={onRefresh} size="compact-xs" variant="subtle">Refresh</Button>
      </div>
    </div>
  );
}
