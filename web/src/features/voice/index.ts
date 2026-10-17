/** The voice feature's public API (WP-9.7). */
import type { components } from './api/generated';

export { installVoiceLibraryAssetFallback } from './voiceLibraryAssetFallback';
export { installVoiceLibraryFetchDiagnostics } from './voiceLibraryFetchDiagnostics';
export { voiceApiClient } from './api/voiceClient';
export type STTProxyResponse = components['schemas']['STTProxyResponse'];
