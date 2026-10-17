import type { MessageContent, TokenUsage } from './conversation';

export type ProviderMessageRole = 'system' | 'user' | 'assistant' | 'tool';

export type ProviderMessage = {
  role: ProviderMessageRole;
  content: MessageContent[];
};

export type ProviderToolDefinition = {
  id: string;
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
};

export type ModelRequest = {
  provider: string;
  model: string;
  messages: ProviderMessage[];
  tools?: ProviderToolDefinition[];
  jsonMode?: boolean;
  stream?: boolean;
  temperature?: number;
  metadata?: Record<string, unknown>;
};

export type ModelResponse = {
  content: MessageContent[];
  finishReason?: string;
  tokenUsage?: TokenUsage;
  latencyMs?: number;
  raw?: unknown;
};

export type ModelProviderCapabilities = {
  tools: boolean;
  streaming: boolean;
  vision: boolean;
  reasoning: boolean;
  jsonMode: boolean;
};

export interface ModelProvider {
  id: string;
  name: string;
  execute(request: ModelRequest): Promise<ModelResponse>;
  supportsTools(): boolean;
  supportsStreaming(): boolean;
  supportsVision(): boolean;
  supportsReasoning(): boolean;
  supportsJsonMode(): boolean;
}

export function createProviderCapabilitySet(
  capabilities: Partial<ModelProviderCapabilities> = {},
): ModelProviderCapabilities {
  return {
    tools: capabilities.tools ?? false,
    streaming: capabilities.streaming ?? false,
    vision: capabilities.vision ?? false,
    reasoning: capabilities.reasoning ?? false,
    jsonMode: capabilities.jsonMode ?? false,
  };
}

export function providerSupportsRequest(
  provider: Pick<
    ModelProvider,
    'supportsTools' | 'supportsStreaming' | 'supportsVision' | 'supportsReasoning' | 'supportsJsonMode'
  >,
  request: Pick<ModelRequest, 'tools' | 'stream' | 'jsonMode'> & { vision?: boolean; reasoning?: boolean },
): boolean {
  if (request.tools?.length && !provider.supportsTools()) return false;
  if (request.stream && !provider.supportsStreaming()) return false;
  if (request.jsonMode && !provider.supportsJsonMode()) return false;
  if (request.vision && !provider.supportsVision()) return false;
  if (request.reasoning && !provider.supportsReasoning()) return false;
  return true;
}

export function createStaticModelProvider(
  id: string,
  name: string,
  capabilities: Partial<ModelProviderCapabilities>,
  execute: (request: ModelRequest) => Promise<ModelResponse>,
): ModelProvider {
  const resolved = createProviderCapabilitySet(capabilities);

  return {
    id,
    name,
    execute,
    supportsTools: () => resolved.tools,
    supportsStreaming: () => resolved.streaming,
    supportsVision: () => resolved.vision,
    supportsReasoning: () => resolved.reasoning,
    supportsJsonMode: () => resolved.jsonMode,
  };
}
