/** The image-generation feature's public API (WP-9.7). */
import type { components } from './api/generated';

export { imageGenerationModule } from './module';
export type ImageReferenceUploadResponse = components['schemas']['ImageReferenceUploadResponse'];
