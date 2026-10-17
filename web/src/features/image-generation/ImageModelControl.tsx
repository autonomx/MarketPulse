import type { ComponentProps } from 'react';
import {
  ImageModelControl as CompatImageModelControl,
  imageModelGenerationBlockReason,
  selectedImageModel,
} from './ImageModelControlCompat';

export type {
  ImageLocalModelStatus,
  ImageModelAction,
  ImageModelRecord,
  ImageModelStatusView,
} from './ImageModelControlCompat';
export { imageModelGenerationBlockReason, selectedImageModel };

type CompatProps = ComponentProps<typeof CompatImageModelControl>;
type SharedProps = Omit<
  CompatProps,
  'selectedProvider' | 'onSelect' | 'onDownload' | 'onLoad' | 'onUnload'
>;

type MultiModelProps = SharedProps & {
  selectedProvider: string;
  onSelect: (provider: string) => void;
  onDownload: (provider: string, hfToken?: string) => void;
  onLoad: (provider: string) => void;
  onUnload: (provider: string) => void;
};

type LegacyProps = SharedProps & {
  selectedProvider?: never;
  onSelect?: never;
  onDownload?: never;
  onLoad: () => void;
  onUnload: () => void;
};

type ImageModelControlComponent = {
  (props: MultiModelProps): ReturnType<typeof CompatImageModelControl>;
  (props: LegacyProps): ReturnType<typeof CompatImageModelControl>;
};

export const ImageModelControl = CompatImageModelControl as ImageModelControlComponent;
