import {
  DEFAULT_CALENDAR_TOOL_ACTIONS,
  DEFAULT_CONTACTS_TOOL_ACTIONS,
  DEFAULT_GITHUB_TOOL_ACTIONS,
  DEFAULT_GMAIL_TOOL_ACTIONS,
  type ToolAction,
} from './tool-actions';
import { executeToolProposal } from './tool-proposal-client';

export type ToolCategory = 'communication' | 'development' | 'productivity' | 'local_system' | 'automation';

export type ToolMetadata = {
  name: string;
  description: string;
  icon?: string;
  provider?: string;
};

export type ToolConfig = {
  enabled: boolean;
  connectionStatus: 'not_configured' | 'connected' | 'error';
  settings?: Record<string, unknown>;
};

export type ToolConfigValidation = {
  valid: boolean;
  status: ToolConfig['connectionStatus'];
  messages: string[];
};

export type ToolConnectionResult = {
  ok: boolean;
  status: ToolConfig['connectionStatus'];
  message: string;
  checkedAt: string;
};

export type ToolExecutionRequest = {
  toolId: string;
  actionId: string;
  input?: Record<string, unknown>;
  confirm?: boolean;
};

export type ToolExecutionResult = {
  status: 'completed' | 'failed' | 'denied';
  output?: unknown;
  error?: string;
};

export type AssistantTool = {
  id: string;
  category: ToolCategory;
  metadata: ToolMetadata;
  actions: readonly ToolAction[];
  defaultConfig: ToolConfig;
  validateConfig: (config: ToolConfig) => ToolConfigValidation;
  testConnection: (config: ToolConfig) => Promise<ToolConnectionResult>;
  execute: (request: ToolExecutionRequest, config: ToolConfig) => Promise<ToolExecutionResult>;
};

export type AssistantToolRegistry = {
  register: (tool: AssistantTool) => AssistantToolRegistry;
  list: () => AssistantTool[];
  get: (toolId: string) => AssistantTool | undefined;
  getAction: (actionId: string) => { tool: AssistantTool; action: ToolAction } | undefined;
};

export function createAssistantToolRegistry(tools: AssistantTool[] = []): AssistantToolRegistry {
  const registry = new Map<string, AssistantTool>();

  for (const tool of tools) {
    registry.set(tool.id, tool);
  }

  return {
    register(tool) {
      registry.set(tool.id, tool);
      return this;
    },
    list() {
      return Array.from(registry.values());
    },
    get(toolId) {
      return registry.get(toolId);
    },
    getAction(actionId) {
      for (const tool of registry.values()) {
        const action = tool.actions.find((candidate) => candidate.id === actionId);
        if (action) {
          return { tool, action };
        }
      }
      return undefined;
    },
  };
}

export function validateConnectionBackedToolConfig(config: ToolConfig): ToolConfigValidation {
  if (!config.enabled) {
    return {
      valid: true,
      status: 'not_configured',
      messages: ['Tool is disabled.'],
    };
  }

  if (config.connectionStatus !== 'connected') {
    return {
      valid: false,
      status: config.connectionStatus,
      messages: ['Tool must be connected before actions can run.'],
    };
  }

  return {
    valid: true,
    status: 'connected',
    messages: [],
  };
}

export function createConfiguredConnectionResult(config: ToolConfig, label: string): Promise<ToolConnectionResult> {
  return Promise.resolve({
    ok: config.enabled && config.connectionStatus === 'connected',
    status: config.connectionStatus,
    message:
      config.enabled && config.connectionStatus === 'connected'
        ? `${label} connection is available.`
        : `${label} connection needs setup.`,
    checkedAt: new Date(0).toISOString(),
  });
}

export async function executeAssistantToolRequest(request: ToolExecutionRequest, config: ToolConfig): Promise<ToolExecutionResult> {
  if (!config.enabled || config.connectionStatus !== 'connected') {
    return { status: 'denied', error: 'Tool is not connected.' };
  }
  if (typeof fetch !== 'function') {
    return { status: 'failed', error: 'Backend tool execution endpoint is unavailable.' };
  }
  try {
    const payload = await executeToolProposal({
      tool_id: request.toolId, action_id: request.actionId, input: request.input ?? {},
    }, request.confirm);
    if (payload.execution_result.error) {
      return { status: 'failed', error: payload.execution_result.error };
    }
    return { status: 'completed', output: payload.execution_result.output };
  } catch (error) {
    return { status: 'failed', error: error instanceof Error ? error.message : 'Backend tool execution failed.' };
  }
}

function createConnectionBackedTool(input: {
  id: string;
  category: ToolCategory;
  metadata: ToolMetadata;
  actions: readonly ToolAction[];
}): AssistantTool {
  return {
    ...input,
    defaultConfig: {
      enabled: false,
      connectionStatus: 'not_configured',
    },
    validateConfig: validateConnectionBackedToolConfig,
    testConnection: (config) => createConfiguredConnectionResult(config, input.metadata.name),
    execute: executeAssistantToolRequest,
  };
}

export const DEFAULT_ASSISTANT_TOOLS = [
  createConnectionBackedTool({
    id: 'gmail',
    category: 'communication',
    metadata: {
      name: 'Gmail',
      description: 'Read, draft, send, label, archive, and delete Gmail messages with approval controls.',
      icon: '✉',
      provider: 'Google',
    },
    actions: DEFAULT_GMAIL_TOOL_ACTIONS,
  }),
  createConnectionBackedTool({
    id: 'calendar',
    category: 'productivity',
    metadata: {
      name: 'Google Calendar',
      description: 'Read availability, manage events, and respond to invitations.',
      icon: '◷',
      provider: 'Google',
    },
    actions: DEFAULT_CALENDAR_TOOL_ACTIONS,
  }),
  createConnectionBackedTool({
    id: 'contacts',
    category: 'productivity',
    metadata: {
      name: 'Google Contacts',
      description: 'Resolve contacts for email and calendar workflows.',
      icon: '◎',
      provider: 'Google',
    },
    actions: DEFAULT_CONTACTS_TOOL_ACTIONS,
  }),
  createConnectionBackedTool({
    id: 'github',
    category: 'development',
    metadata: {
      name: 'GitHub',
      description: 'Read repositories, manage pull requests, inspect CI, and perform governed repo actions.',
      icon: '⌘',
      provider: 'GitHub',
    },
    actions: DEFAULT_GITHUB_TOOL_ACTIONS,
  }),
] as const;

export function createDefaultAssistantToolRegistry(): AssistantToolRegistry {
  return createAssistantToolRegistry([...DEFAULT_ASSISTANT_TOOLS]);
}
