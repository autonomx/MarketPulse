import { useEffect, useState } from 'react';
import { ResearchCredentialSettings } from './ResearchCredentialSettings';
import { SettingsAdvanced, SettingsField, SettingsSection, SettingsStatusRow } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';
import type { ResearchProvider } from './settingsDocumentTypes';
import type { components } from './api/generated';
import { unwrap } from '../../api/http';
import { api } from './api/gateway';


type ResearchRuntimeStatus = components['schemas']['ResearchRuntimeStatus'];

const PROVIDERS: Array<{ value: ResearchProvider; label: string }> = [
  { value: 'brave', label: 'Brave Search · API-backed general web search' },
  { value: 'tavily', label: 'Tavily · API-backed general web search' },
  { value: 'playwright', label: 'Playwright · local browser search' },
  { value: 'duckduckgo', label: 'DuckDuckGo · keyless limited fallback' },
];

const providerLabel = (provider: string) => PROVIDERS.find((item) => item.value === provider)?.label.split(' · ')[0] ?? provider;

export function ResearchSettingsSection() {
  const { state, dispatch } = useSettingsProfileContext();
  const value = state.draft.assistant;
  const [runtime, setRuntime] = useState<ResearchRuntimeStatus>();
  const [statusMessage, setStatusMessage] = useState('Checking research runtime…');

  useEffect(() => {
    let active = true;
    unwrap(api.GET('/api/assistant/research/status')).then((result: ResearchRuntimeStatus) => {
      if (!active) return;
      setRuntime(result);
      setStatusMessage('Research runtime status loaded.');
    }).catch((error) => {
      if (!active) return;
      setStatusMessage(error instanceof Error ? error.message : 'Research runtime status is unavailable.');
    });
    return () => { active = false; };
  }, []);

  const numberField = (
    label: string,
    path: string,
    current: number,
    min: number,
    max: number,
    step = 1,
  ) => (
    <SettingsField label={label}>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={current}
        onChange={(event) => dispatch({ type: 'update', path, value: Number(event.currentTarget.value) })}
      />
    </SettingsField>
  );

  const setPrimaryProvider = (provider: ResearchProvider) => {
    dispatch({ type: 'update', path: 'assistant.researchProvider', value: provider });
    dispatch({
      type: 'update',
      path: 'assistant.researchProviderFallbacks',
      value: value.researchProviderFallbacks.filter((item) => item !== provider),
    });
  };

  const setFallbackSlot = (index: number, provider: ResearchProvider | '') => {
    const slots: Array<ResearchProvider | ''> = [
      value.researchProviderFallbacks[0] ?? '',
      value.researchProviderFallbacks[1] ?? '',
      value.researchProviderFallbacks[2] ?? '',
    ];
    if (provider) {
      for (let slotIndex = 0; slotIndex < slots.length; slotIndex += 1) {
        if (slotIndex !== index && slots[slotIndex] === provider) slots[slotIndex] = '';
      }
    }
    slots[index] = provider;
    const next = slots.filter(
      (item): item is ResearchProvider => Boolean(item) && item !== value.researchProvider,
    );
    dispatch({ type: 'update', path: 'assistant.researchProviderFallbacks', value: next });
  };

  return (
    <SettingsSection
      title="Web research"
      description="Choose the primary web search provider, explicit fallback priority, and API-backed provider credentials used by Quick Search and Deep Research."
      scope="module"
    >
      <div className="settings-form-grid">
        <SettingsField label="Primary search provider" help="Brave is the recommended default. If it is unavailable or returns no usable result, Omnix proceeds through the configured fallback order.">
          <select value={value.researchProvider} onChange={(event) => setPrimaryProvider(event.currentTarget.value as ResearchProvider)}>
            {PROVIDERS.map((provider) => <option key={provider.value} value={provider.value}>{provider.label}</option>)}
          </select>
        </SettingsField>
        {numberField('Quick results', 'assistant.researchMaxResults', value.researchMaxResults, 1, 8)}
        {[0, 1, 2].map((index) => {
          const current = value.researchProviderFallbacks[index] ?? '';
          const selectedElsewhere = new Set(
            value.researchProviderFallbacks.filter((_, slotIndex) => slotIndex !== index),
          );
          return (
            <SettingsField
              key={index}
              label={`Fallback ${index + 1}`}
              help={index === 0 ? 'Fallbacks are tried in order. Credentialed providers without a configured key are skipped.' : undefined}
            >
              <select
                value={current}
                onChange={(event) => setFallbackSlot(index, event.currentTarget.value as ResearchProvider | '')}
              >
                <option value="">None</option>
                {PROVIDERS.filter((provider) => (
                  provider.value !== value.researchProvider
                  && (provider.value === current || !selectedElsewhere.has(provider.value))
                )).map((provider) => (
                  <option key={provider.value} value={provider.value}>{provider.label}</option>
                ))}
              </select>
            </SettingsField>
          );
        })}
      </div>

      <ResearchCredentialSettings />

      <div className="settings-toggle-list">
        <label><input type="checkbox" checked={value.researchDeepEnabled} onChange={(event) => dispatch({ type: 'update', path: 'assistant.researchDeepEnabled', value: event.currentTarget.checked })} /><span>Enable Deep Research when released for this session</span></label>
        <label><input type="checkbox" checked={value.researchShowDiagnostics} onChange={(event) => dispatch({ type: 'update', path: 'assistant.researchShowDiagnostics', value: event.currentTarget.checked })} /><span>Show research diagnostics and source details</span></label>
        <label><input type="checkbox" checked={value.researchHermesPlannerEnabled} onChange={(event) => dispatch({ type: 'update', path: 'assistant.researchHermesPlannerEnabled', value: event.currentTarget.checked })} /><span>Prefer Hermes only when its separate release gate is active</span></label>
      </div>

      <SettingsAdvanced label="Budgets, cache, and retention">
        <div className="settings-form-grid">
          {numberField('Maximum steps', 'assistant.researchMaxSteps', value.researchMaxSteps, 1, 12)}
          {numberField('Maximum queries', 'assistant.researchMaxQueries', value.researchMaxQueries, 1, 10)}
          {numberField('Maximum sources', 'assistant.researchMaxSources', value.researchMaxSources, 1, 30)}
          {numberField('Maximum page extracts', 'assistant.researchMaxExtracts', value.researchMaxExtracts, 0, 20)}
          {numberField('Search cache TTL (seconds)', 'assistant.researchSearchCacheTtlSeconds', value.researchSearchCacheTtlSeconds, 1, 86400)}
          {numberField('Extraction cache TTL (seconds)', 'assistant.researchExtractionCacheTtlSeconds', value.researchExtractionCacheTtlSeconds, 1, 604800)}
          {numberField('Raw page retention (days)', 'assistant.researchRawRetentionDays', value.researchRawRetentionDays, 0, 365)}
          {numberField('Manifest retention (days)', 'assistant.researchManifestRetentionDays', value.researchManifestRetentionDays, 1, 3650)}
        </div>
      </SettingsAdvanced>

      <div role="group" className="settings-status-list" aria-label="Research runtime status">
        <SettingsStatusRow
          label="Primary provider"
          value={runtime ? `${providerLabel(runtime.provider.provider)} · ${runtime.provider.available ? 'Ready' : 'Credential required'}` : 'Checking'}
          tone={runtime?.provider.available ? 'ready' : runtime ? 'warning' : 'idle'}
        />
        <SettingsStatusRow
          label="Runtime search order"
          value={runtime?.provider_chain?.length
            ? runtime.provider_chain.map((item, index) => `${index + 1}. ${providerLabel(item.provider)}${item.available ? '' : ' (unavailable)'}`).join(' → ')
            : 'Checking'}
          tone="neutral"
        />
        <SettingsStatusRow label="Primary coverage" value={runtime?.provider.coverage ?? 'Not reported'} tone="neutral" />
        <SettingsStatusRow
          label="Primary credentials"
          value={runtime ? (runtime.provider.credential_required ? (runtime.provider.credential_configured ? 'Configured' : 'Not configured; fallback will be used') : 'Not required') : 'Checking'}
          tone={runtime?.provider.available ? 'ready' : runtime ? 'warning' : 'idle'}
        />
        <SettingsStatusRow
          label="Master release"
          value={runtime ? (runtime.release.master_enabled ? 'Enabled' : 'Rollback active') : 'Checking'}
          tone={runtime?.release.master_enabled ? 'ready' : runtime ? 'warning' : 'idle'}
        />
        <SettingsStatusRow
          label="Quick Search release"
          value={runtime ? `${runtime.release.availability.quick ? 'Available' : 'Unavailable'} · ${runtime.release.quick_percentage}% cohort` : 'Checking'}
          tone={runtime?.release.availability.quick ? 'ready' : runtime ? 'warning' : 'idle'}
        />
        <SettingsStatusRow
          label="Deep Research release"
          value={runtime ? `${runtime.release.availability.deep ? 'Available' : 'Unavailable'} · ${runtime.release.deep_local_percentage}% cohort` : 'Checking'}
          tone={runtime?.release.availability.deep ? 'ready' : 'idle'}
        />
        <SettingsStatusRow
          label="Hermes planner release"
          value={runtime ? `${runtime.release.availability.hermes_planner ? 'Available' : 'Not released'} · ${runtime.release.hermes_percentage}% cohort` : 'Checking'}
          tone={runtime?.release.availability.hermes_planner ? 'ready' : 'idle'}
        />
        <SettingsStatusRow
          label="Legacy request aliases"
          value={runtime ? (runtime.compatibility.aliases_enabled ? `Temporary · sunset ${runtime.compatibility.sunset ?? 'not scheduled'}` : 'Disabled') : 'Checking'}
          tone={runtime?.compatibility.aliases_enabled ? 'warning' : runtime ? 'ready' : 'idle'}
        />
        <SettingsStatusRow
          label="Legacy alias requests"
          value={runtime ? `${runtime.compatibility.total_legacy_requests} observed · canonical ${runtime.compatibility.canonical_field}` : 'Checking'}
          tone={runtime?.compatibility.total_legacy_requests ? 'warning' : 'neutral'}
        />
      </div>
      <p className="settings-inline-status" role="status">{statusMessage}</p>
    </SettingsSection>
  );
}