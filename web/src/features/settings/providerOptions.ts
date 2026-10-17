import type { ProviderFacadePayload } from '../../api/client';

export type ProviderOption = { id: string; label: string };

function settingsProviderId(id: string, family: string): string {
  const prefix = `${family}:`;
  return id.startsWith(prefix) ? id.slice(prefix.length) : id;
}

export function providerOptions(payload: ProviderFacadePayload | undefined, capability: string): ProviderOption[] {
  const options = new Map<string, ProviderOption>();
  for (const provider of payload?.providers ?? []) {
    if (!(provider.capabilities.some((item) => item === capability) || provider.family === capability)) continue;
    const id = settingsProviderId(provider.id, provider.family);
    if (!options.has(id)) {
      options.set(id, { id, label: provider.label || id });
    }
  }
  return [...options.values()]
    .sort((left, right) => left.label.localeCompare(right.label));
}

export function modelOptions(payload: ProviderFacadePayload | undefined, providerId: string, capability = 'chat'): ProviderOption[] {
  return (payload?.models ?? [])
    .filter((model) => {
      const normalizedProviderId = settingsProviderId(model.provider_id, model.provider_id.split(':', 1)[0] ?? '');
      const providerMatches = !providerId || model.provider_id === providerId || normalizedProviderId === providerId;
      return providerMatches && model.capabilities.some((item) => item === capability);
    })
    .map((model) => ({ id: model.id, label: model.label || model.id, catalog: model.metadata?.source === 'catalog' }))
    // A provider's own catalog (the Claude CLI's) keeps its order, newest first; other models sort by name.
    .sort((left, right) => Number(right.catalog) - Number(left.catalog) || (left.catalog ? 0 : left.label.localeCompare(right.label)))
    .map(({ id, label }) => ({ id, label }));
}
