import { describe, expect, it } from 'vitest';
import { createProviderCapabilitySet, providerSupportsRequest } from './provider';

describe('provider abstraction', () => {
  it('normalizes capabilities and validates requests', () => {
    const capabilities = createProviderCapabilitySet({ streaming: true, jsonMode: true });
    const provider = {
      supportsTools: () => capabilities.tools,
      supportsStreaming: () => capabilities.streaming,
      supportsVision: () => capabilities.vision,
      supportsReasoning: () => capabilities.reasoning,
      supportsJsonMode: () => capabilities.jsonMode,
    };

    expect(capabilities.streaming).toBe(true);
    expect(capabilities.tools).toBe(false);
    expect(providerSupportsRequest(provider, { stream: true, jsonMode: true })).toBe(true);
    expect(providerSupportsRequest(provider, { tools: [{ id: 't', name: 'Search', description: 'Search' }] })).toBe(false);
  });
});
