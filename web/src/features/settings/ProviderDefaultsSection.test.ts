import { describe, expect, it } from 'vitest';
import type { ProviderFacadePayload } from '../../api/client';
import { codexReasoningOptions, providerModelOptions } from './ProviderDefaultsSection';
import { modelOptions } from './providerOptions';
import { fixture } from '../../test/fixture';

describe('Claude CLI model options', () => {
  const claudeModel = (modelId: string, label: string) => fixture<ProviderFacadePayload['models'][number]>({
    id: `llm:claude_cli:${modelId}`,
    label,
    provider_id: 'llm:claude_cli',
    capabilities: ['chat'],
    location: 'remote',
    metadata: { source: 'catalog', model_id: modelId },
  });
  const payload = {
    providers: [],
    models: [claudeModel('claude-opus-5-5', 'Opus 5.5'), claudeModel('claude-fable-5-1', 'Fable 5.1'), claudeModel('sonnet', 'Latest Sonnet (sonnet)')],
  };

  it('lists every catalog model for the default chat model, in the CLI order', () => {
    expect(modelOptions(payload, 'llm:claude_cli').map((option) => option.label)).toEqual(['Opus 5.5', 'Fable 5.1', 'Latest Sonnet (sonnet)']);
  });

  it('offers the catalog in the provider settings by bare model id, keeping an unknown configured model', () => {
    expect(providerModelOptions(payload, 'llm:claude_cli', 'sonnet').map((option) => option.id)).toEqual(['claude-opus-5-5', 'claude-fable-5-1', 'sonnet']);
    expect(providerModelOptions(payload, 'llm:claude_cli', 'claude-custom')[0]).toEqual({ id: 'claude-custom', label: 'claude-custom (unavailable)' });
  });
});

describe('Codex reasoning effort options', () => {
  it('offers extra high for GPT-5.6 Luna even when the catalog stops at high', () => {
    const options = codexReasoningOptions(undefined, 'gpt-5.6-luna', 'high');

    expect(options).toEqual([
      { id: 'none', label: 'Off (no reasoning)' },
      { id: 'low', label: 'low' },
      { id: 'medium', label: 'medium' },
      { id: 'high', label: 'high' },
      { id: 'xhigh', label: 'extra high' },
    ]);
  });

  it('does not add Luna-only extra high to other models', () => {
    const options = codexReasoningOptions(undefined, 'gpt-5.6-sol', 'high');

    expect(options.map((option) => option.id)).toEqual(['none', 'low', 'medium', 'high']);
  });

  it('does not duplicate extra high when Codex advertises it', () => {
    const options = codexReasoningOptions({
      providers: [],
      models: [fixture({
        id: 'llm:chatgpt_codex:gpt-5.6-luna',
        label: 'GPT-5.6 Luna',
        provider_id: 'llm:chatgpt_codex',
        capabilities: ['chat'],
        location: 'unknown',
        metadata: { supported_reasoning_efforts: ['none', 'high', 'xhigh'] },
      })],
    }, 'gpt-5.6-luna', 'xhigh');

    expect(options.map((option) => option.id)).toEqual(['none', 'high', 'xhigh']);
  });
});
