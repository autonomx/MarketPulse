import { Button, Text } from '@mantine/core';
import { OmnixStatusPill } from '../../design/primitives';
import {
  ImageModelControl as MultiModelControl,
  imageModelGenerationBlockReason as multiModelBlockReason,
  type ImageModelStatusView,
} from './ImageModelSelectorControl';

export type { ImageLocalModelStatus, ImageModelRecord, ImageModelStatusView } from './ImageModelSelectorControl';
export { selectedImageModel } from './ImageModelSelectorControl';

export type ImageModelAction = { type: 'download' | 'load' | 'unload'; provider: string } | 'load' | 'unload' | null;

type Props = {
  status?: ImageModelStatusView;
  selectedProvider?: string;
  statusLoading: boolean;
  action: ImageModelAction;
  error?: string;
  onSelect?: (provider: string) => void;
  onDownload?: (provider: string, hfToken?: string) => void;
  onLoad: (provider?: string) => void;
  onUnload: (provider?: string) => void;
  onRefresh: () => void;
};

export function ImageModelControl(props: Props) {
  if (props.selectedProvider || props.onSelect || props.onDownload) {
    const provider = props.selectedProvider || props.status?.provider || 'flux_klein';
    const action = typeof props.action === 'string' ? { type: props.action, provider } : props.action;
    return <MultiModelControl {...props} selectedProvider={provider} action={action} onSelect={props.onSelect || (() => undefined)} onDownload={props.onDownload || (() => undefined)} />;
  }

  const loaded = Boolean(props.status?.loaded);
  const complete = props.status?.local_model?.complete !== false;
  const state = typeof props.action === 'string' ? `${props.action}ing` : props.status?.state || 'checking';
  const canLoad = Boolean(props.status?.enabled && complete && !loaded && !props.action && !props.statusLoading);
  const canUnload = Boolean(loaded && !props.action && !props.statusLoading);
  const missing = props.status?.local_model?.missing ?? [];
  return (
    <section className={`image-model-control ${loaded ? 'loaded' : 'unloaded'}`} aria-label="Image model control" aria-live="polite">
      <div className="image-model-control-copy">
        <div className="image-model-control-title-row">
          <strong>{props.status?.model || 'FLUX.2 [klein] 4B'}</strong>
          <OmnixStatusPill>{state}</OmnixStatusPill>
        </div>
        {props.status?.local_model?.local_dir ? <Text className="image-model-path" size="xs">{props.status.local_model.local_dir}</Text> : null}
        {!complete ? <Text c="red" size="xs" role="alert">Local model files are incomplete{missing.length ? `: ${missing.join(', ')}` : '.'}</Text> : null}
        {props.error ? <Text c="red" size="xs" role="alert">{props.error}</Text> : null}
      </div>
      <div className="image-model-control-actions">
        {loaded
          ? <Button color="red" disabled={!canUnload} onClick={() => props.onUnload()} size="compact-sm" variant="light">Unload Model</Button>
          : <Button disabled={!canLoad} onClick={() => props.onLoad()} size="compact-sm">Load Model</Button>}
        <Button disabled={Boolean(props.action)} loading={props.statusLoading} onClick={props.onRefresh} size="compact-sm" variant="subtle">Refresh Status</Button>
      </div>
    </section>
  );
}

export function imageModelGenerationBlockReason(
  status: ImageModelStatusView | undefined,
  selectedProviderOrLoading: string | boolean,
  statusLoadingOrError: boolean,
  statusError?: boolean,
): string | undefined {
  if (typeof selectedProviderOrLoading === 'string') {
    return multiModelBlockReason(status, selectedProviderOrLoading, statusLoadingOrError, Boolean(statusError));
  }
  if (selectedProviderOrLoading) return 'Checking FLUX.2 [klein] 4B model status.';
  if (statusLoadingOrError || !status || status.error || status.ok === false || status.state === 'unavailable') return 'The image model service is unavailable. Check the launcher and refresh status.';
  if (!status.enabled) return 'Image generation is disabled for this startup.';
  if (status.local_model?.complete === false) return 'The local FLUX.2 [klein] 4B model files are incomplete.';
  if (!status.loaded) return 'Load FLUX.2 [klein] 4B before generating an image.';
  return undefined;
}
