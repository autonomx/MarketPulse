 
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  canExecuteToolAction,
  type ApprovalPolicy,
  type ToolAction,
} from '../workspace/tool-actions';
import { createDefaultAssistantToolRegistry, type AssistantTool, type ToolConfig } from '../workspace/tool-registry';
import {
  disconnectAssistantToolAccount,
  fetchAssistantToolsConfig,
  saveAssistantToolOAuthClient,
  saveAssistantToolsConfig,
  startAssistantToolConnection,
  type AssistantToolsConfigPayload,
} from './assistantToolConfigClient';

export type AssistantToolSettingsPanelProps = {
  enabledToolCount: number;
  initialConnectionMessage?: string | null;
  initialToolId?: string | null;
  toolExecutionRows: number;
  onShowExecutionPanel: () => void;
};

const assistantToolConfigQueryKey = ['assistant-tools', 'config'] as const;

const approvalPolicies: Array<{ value: ApprovalPolicy; label: string }> = [
  { value: 'allow_automatic', label: 'Allow automatic' },
  { value: 'ask_sensitive', label: 'Ask for sensitive' },
  { value: 'always_ask', label: 'Always ask' },
  { value: 'disabled', label: 'Disabled' },
];

export function AssistantToolSettingsPanel({ enabledToolCount, initialConnectionMessage = null, initialToolId = null, onShowExecutionPanel, toolExecutionRows }: AssistantToolSettingsPanelProps) {
  const registry = useMemo(() => createDefaultAssistantToolRegistry(), []);
  const tools = useMemo(() => registry.list(), [registry]);
  const queryClient = useQueryClient();
  const [activeToolId, setActiveToolId] = useState<string | null>(initialToolId);
  const [oauthClientId, setOauthClientId] = useState('');
  const [oauthClientSecret, setOauthClientSecret] = useState('');
  const [connectionStatusMessage, setConnectionStatusMessage] = useState<string | null>(initialConnectionMessage);

  const configQuery = useQuery({
    queryKey: assistantToolConfigQueryKey,
    queryFn: fetchAssistantToolsConfig,
  });

  const saveMutation = useMutation({
    mutationFn: saveAssistantToolsConfig,
    onSuccess(saved) {
      queryClient.setQueryData(assistantToolConfigQueryKey, saved);
    },
  });

  const configPayload = configQuery.data ?? createFallbackConfigPayload(tools);
  const activeTool = activeToolId ? registry.get(activeToolId) : undefined;
  const activeToolRecord = activeTool ? getToolRecord(configPayload, activeTool) : undefined;
  const activeConfig = activeTool && activeToolRecord ? toToolConfig(activeToolRecord) : undefined;
  const activeActions = activeTool && activeToolRecord ? mergeActionConfig(activeTool, activeToolRecord) : [];
  const enabledActions = activeActions.filter((action) => action.enabled).length;

  function persistConfig(updater: (payload: AssistantToolsConfigPayload) => AssistantToolsConfigPayload): void {
    const basePayload = configQuery.data ?? createFallbackConfigPayload(tools);
    const nextPayload = updater(basePayload);
    queryClient.setQueryData(assistantToolConfigQueryKey, nextPayload);
    saveMutation.mutate(nextPayload);
  }

  function setToolEnabled(tool: AssistantTool, enabled: boolean): void {
    persistConfig((payload) => updateToolRecord(payload, tool.id, (record) => ({
      ...record,
      enabled,
    })));
  }

  async function disconnectToolAccount(tool: AssistantTool): Promise<void> {
    // The server deletes the stored token (and revokes it at the provider);
    // clearing the label alone would leave the token usable.
    const result = await disconnectAssistantToolAccount(tool.id);
    await queryClient.invalidateQueries({ queryKey: assistantToolConfigQueryKey });
    setConnectionStatusMessage(`${tool.metadata.name}: ${result.message}`);
  }

  function setActionEnabled(tool: AssistantTool, action: ToolAction, enabled: boolean): void {
    persistConfig((payload) => updateActionRecord(payload, tool.id, action.id, (record) => ({ ...record, enabled })));
  }

  function setActionPolicy(tool: AssistantTool, action: ToolAction, approvalPolicy: ApprovalPolicy): void {
    persistConfig((payload) => updateActionRecord(payload, tool.id, action.id, (record) => ({ ...record, approval_policy: approvalPolicy })));
  }

  async function testToolConnection(tool: AssistantTool, config: ToolConfig): Promise<void> {
    const result = await tool.testConnection(config);
    setConnectionStatusMessage(result.message);
  }

  async function connectToolAccount(tool: AssistantTool): Promise<void> {
    const result = await startAssistantToolConnection(tool.id);
    setConnectionStatusMessage(result.message);
    if (result.configured && result.auth_url) {
      window.location.assign(result.auth_url);
    }
  }

  async function saveOauthAppAndConnect(tool: AssistantTool): Promise<void> {
    const result = await saveAssistantToolOAuthClient(tool.id, {
      client_id: oauthClientId,
      client_secret: oauthClientSecret,
    });
    setConnectionStatusMessage(result.message);
    if (result.configured && result.auth_url) {
      window.location.assign(result.auth_url);
    }
  }

  if (activeTool && activeToolRecord && activeConfig) {
    const connectionLabel = accountConnectionLabel(activeTool);
    return (
      <section className="assistant-view-panel assistant-tool-settings-panel" aria-label={`${activeTool.metadata.name} configuration view`}>
        <div className="assistant-tool-detail-header">
          <button type="button" onClick={() => setActiveToolId(null)}>Back to tools</button>
          <div>
            <p className="eyebrow">Tool configuration</p>
            <h2>{activeTool.metadata.name}</h2>
          </div>
          <ToolStatusBadge config={activeConfig} />
        </div>
        <p>{activeTool.metadata.description}</p>
        {configQuery.isError ? <p className="assistant-view-note">Tool configuration could not be loaded. Safe local defaults are shown until the backend is reachable.</p> : null}
        <section className="assistant-tool-connection-panel" aria-label={`${activeTool.metadata.name} account connection`}>
          <header>
            <div>
              <h3>{connectionLabel}</h3>
              <p>{activeConfig.connectionStatus === 'connected' ? `${activeTool.metadata.name} can use ${connectedAccountLabel(activeToolRecord)}.` : `Connect ${activeTool.metadata.name} to a real account before enabled actions can run.`}</p>
            </div>
            <ToolStatusBadge config={activeConfig} />
          </header>
          <dl className="assistant-settings-list">
            <div><dt>Provider</dt><dd>{activeTool.metadata.provider ?? 'Omnix'}</dd></div>
            <div><dt>Category</dt><dd>{activeTool.category.replace('_', ' ')}</dd></div>
            <div><dt>Connection</dt><dd>{activeConfig.connectionStatus.replace('_', ' ')}</dd></div>
            <div><dt>Connected account</dt><dd>{connectedAccountLabel(activeToolRecord)}</dd></div>
            <div><dt>Enabled actions</dt><dd>{enabledActions}/{activeActions.length}</dd></div>
            <div><dt>Persistence</dt><dd>{saveMutation.isPending ? 'Saving' : configQuery.isLoading ? 'Loading' : 'Backend saved'}</dd></div>
          </dl>
          <div className="assistant-tool-config-actions">
            <button type="button" onClick={() => void connectToolAccount(activeTool)}>Connect real account</button>
            <button type="button" onClick={() => void disconnectToolAccount(activeTool)} disabled={activeConfig.connectionStatus !== 'connected'}>Disconnect account</button>
            <button type="button" onClick={() => setToolEnabled(activeTool, !activeConfig.enabled)}>{activeConfig.enabled ? 'Disable tool' : 'Enable tool'}</button>
            <button type="button" onClick={() => void testToolConnection(activeTool, activeConfig)}>Test connection</button>
            <button type="button" onClick={onShowExecutionPanel}>Show execution panel</button>
          </div>
          {connectionStatusMessage ? <p className="assistant-view-note" role="status">{connectionStatusMessage}</p> : null}
          {activeTool.metadata.provider === 'Google' || activeTool.metadata.provider === 'GitHub' ? (
            <form
              className="assistant-tool-oauth-form"
              onSubmit={(event) => {
                event.preventDefault();
                void saveOauthAppAndConnect(activeTool);
              }}
            >
              <label>
                <span>{activeTool.metadata.provider} OAuth client ID</span>
                <input autoComplete="off" onChange={(event) => setOauthClientId(event.currentTarget.value)} type="text" value={oauthClientId} />
              </label>
              <label>
                <span>{activeTool.metadata.provider} OAuth client secret</span>
                <input autoComplete="off" onChange={(event) => setOauthClientSecret(event.currentTarget.value)} type="password" value={oauthClientSecret} />
              </label>
              <button type="submit">Save OAuth app and connect</button>
            </form>
          ) : null}
        </section>
        <div role="group" className="assistant-tool-action-list" aria-label={`${activeTool.metadata.name} actions`}>
          {activeActions.map((action) => {
            const gate = canExecuteToolAction(action);
            return (
              <article className="assistant-tool-action-row" key={action.id}>
                <div>
                  <h4>{action.label}</h4>
                  <p>{action.description}</p>
                  <small>{action.id} - {action.category} - {action.riskLevel} risk{action.isDestructive ? ' - destructive' : ''}{action.requiresConfirmation ? ' - confirmation required' : ''}</small>
                </div>
                <label className="assistant-tool-toggle">
                  <span>{action.enabled ? 'Enabled' : 'Disabled'}</span>
                  <input checked={action.enabled} onChange={(event) => setActionEnabled(activeTool, action, event.currentTarget.checked)} type="checkbox" />
                </label>
                <label className="assistant-tool-policy-select">
                  <span>Approval</span>
                  <select value={action.approvalPolicy} onChange={(event) => setActionPolicy(activeTool, action, event.currentTarget.value as ApprovalPolicy)}>
                    {approvalPolicies.map((policy) => <option key={policy.value} value={policy.value}>{policy.label}</option>)}
                  </select>
                </label>
                <strong>{gate.allowed ? gate.approvalRequired ? 'Approval required' : 'Ready' : 'Blocked'}</strong>
              </article>
            );
          })}
        </div>
      </section>
    );
  }

  return (
    <section className="assistant-view-panel assistant-tool-settings-panel" aria-label="Tools view">
      <p className="eyebrow">Omnix Assistant</p>
      <h2>Tools</h2>
      <p>Configure assistant-only tool access, action approval, and connection readiness. Every action is governed before it can become an executable capability.</p>
      {configQuery.isError ? <p className="assistant-view-note">Tool configuration could not be loaded. Safe local defaults are shown until the backend is reachable.</p> : null}
      <div role="group" className="assistant-tool-config-list" aria-label="Registered assistant tools">
        {tools.map((tool) => {
          const record = getToolRecord(configPayload, tool);
          const config = toToolConfig(record);
          const actions = mergeActionConfig(tool, record);
          return (
            <ToolConfigCard
              actionCount={actions.length}
              config={config}
              enabledActionCount={actions.filter((action) => action.enabled).length}
              key={tool.id}
              onConfigure={() => {
                setConnectionStatusMessage(null);
                setActiveToolId(tool.id);
              }}
              onToggle={(enabled) => setToolEnabled(tool, enabled)}
              tool={tool}
            />
          );
        })}
      </div>
      <p className="assistant-view-note">{enabledToolCount} tools active - {toolExecutionRows} replayed execution rows</p>
    </section>
  );
}

function accountConnectionLabel(tool: AssistantTool): string {
  if (tool.metadata.provider === 'Google') return 'Connect Google account';
  if (tool.metadata.provider === 'GitHub') return 'Connect GitHub account';
  return `Connect ${tool.metadata.name} account`;
}

function connectedAccountLabel(record: ReturnType<typeof getToolRecord>): string {
  if (record.account_email && record.account_label) return `${record.account_label} (${record.account_email})`;
  if (record.account_email) return record.account_email;
  if (record.account_label) return record.account_label;
  return record.connection_status === 'connected' ? 'Unknown account' : 'No account connected';
}

function createFallbackConfigPayload(tools: AssistantTool[]): AssistantToolsConfigPayload {
  return {
    tools: tools.map((tool) => ({
      actions: tool.actions.map((action) => ({
        action_id: action.id,
        enabled: action.enabled,
        approval_policy: action.approvalPolicy,
      })),
      connection_status: tool.defaultConfig.connectionStatus,
      approval_policy: null,
      account_label: null,
      account_email: null,
      connected_at: null,
      enabled: tool.defaultConfig.enabled,
      tool_id: tool.id,
    })),
  };
}

function getToolRecord(payload: AssistantToolsConfigPayload, tool: AssistantTool) {
  return payload.tools.find((record) => record.tool_id === tool.id) ?? createFallbackConfigPayload([tool]).tools[0];
}

function toToolConfig(record: ReturnType<typeof getToolRecord>): ToolConfig {
  return {
    enabled: record.enabled,
    connectionStatus: record.connection_status,
  };
}

function mergeActionConfig(tool: AssistantTool, record: ReturnType<typeof getToolRecord>): ToolAction[] {
  const actionConfig = new Map(record.actions.map((action) => [action.action_id, action]));
  return tool.actions.map((action) => {
    const config = actionConfig.get(action.id);
    return config ? { ...action, enabled: config.enabled, approvalPolicy: config.approval_policy } : action;
  });
}

function updateToolRecord(
  payload: AssistantToolsConfigPayload,
  toolId: string,
  updater: (record: AssistantToolsConfigPayload['tools'][number]) => AssistantToolsConfigPayload['tools'][number],
): AssistantToolsConfigPayload {
  return {
    tools: payload.tools.map((record) => (record.tool_id === toolId ? updater(record) : record)),
  };
}

function updateActionRecord(
  payload: AssistantToolsConfigPayload,
  toolId: string,
  actionId: string,
  updater: (record: AssistantToolsConfigPayload['tools'][number]['actions'][number]) => AssistantToolsConfigPayload['tools'][number]['actions'][number],
): AssistantToolsConfigPayload {
  return updateToolRecord(payload, toolId, (toolRecord) => ({
    ...toolRecord,
    actions: toolRecord.actions.map((action) => (action.action_id === actionId ? updater(action) : action)),
  }));
}

function ToolConfigCard({ actionCount, config, enabledActionCount, onConfigure, onToggle, tool }: { actionCount: number; config: ToolConfig; enabledActionCount: number; onConfigure: () => void; onToggle: (enabled: boolean) => void; tool: AssistantTool }) {
  return (
    <article>
      <div>
        <h3>{tool.metadata.name}</h3>
        <p>{tool.metadata.description}</p>
        <small>{enabledActionCount}/{actionCount} actions enabled</small>
      </div>
      <ToolStatusBadge config={config} />
      <label className="assistant-tool-toggle">
        <span>{config.enabled ? 'Enabled' : 'Disabled'}</span>
        <input checked={config.enabled} onChange={(event) => onToggle(event.currentTarget.checked)} type="checkbox" />
      </label>
      <button type="button" onClick={onConfigure}>Configure</button>
    </article>
  );
}

function ToolStatusBadge({ config }: { config: ToolConfig }) {
  const label = !config.enabled ? 'Disabled' : config.connectionStatus === 'connected' ? 'Enabled' : config.connectionStatus === 'error' ? 'Error' : 'Needs setup';
  return <strong>{label}</strong>;
}
