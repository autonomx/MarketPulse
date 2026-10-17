import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';

// Providers that charge per token. Local providers (LM Studio, llama.cpp) and
// the ChatGPT subscription are free to meter.
const PRICED_PROVIDERS = [
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'cerebras', label: 'Cerebras' },
  { id: 'openai_compatible', label: 'OpenAI-compatible endpoint' },
];

function optionalNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function AgentRunLimitsSection() {
  const { state, dispatch } = useSettingsProfileContext();
  const value = state.draft.agentRuns;
  const update = (path: string, next: unknown) => dispatch({ type: 'update', path: `agentRuns.${path}`, value: next });
  return (
    <SettingsSection title="Agent run limits" scope="module">
      <p>
        Defaults for agent runs started without their own limits. Leave a field empty for no limit. A cost limit
        needs the price of each paid provider the run uses; a run on a paid provider without a price is refused.
      </p>
      <div className="settings-form-grid">
        <SettingsField label="Default output token limit">
          <input
            type="number"
            min={1}
            aria-label="Default output token limit"
            placeholder="No limit"
            value={value.defaultMaxOutputTokens ?? ''}
            onChange={(event) => {
              const next = optionalNumber(event.currentTarget.value);
              update('defaultMaxOutputTokens', next === null ? null : Math.max(1, Math.round(next)));
            }}
          />
        </SettingsField>
        <SettingsField label="Default cost limit (USD)">
          <input
            type="number"
            min={0}
            step={0.01}
            aria-label="Default cost limit (USD)"
            placeholder="No limit"
            value={value.defaultMaxCostUsd ?? ''}
            onChange={(event) => update('defaultMaxCostUsd', optionalNumber(event.currentTarget.value))}
          />
        </SettingsField>
        {PRICED_PROVIDERS.map((provider) => {
          const price = value.providerPrices[provider.id];
          const setPrice = (field: 'inputUsdPerMillion' | 'outputUsdPerMillion', raw: string) => {
            const next = optionalNumber(raw);
            const prices = { ...value.providerPrices };
            if (next === null && !(price && (field === 'inputUsdPerMillion' ? price.outputUsdPerMillion : price.inputUsdPerMillion))) {
              delete prices[provider.id];
            } else {
              prices[provider.id] = {
                inputUsdPerMillion: price?.inputUsdPerMillion ?? 0,
                outputUsdPerMillion: price?.outputUsdPerMillion ?? 0,
                [field]: next ?? 0,
              };
            }
            update('providerPrices', prices);
          };
          return (
            <SettingsField key={provider.id} label={`${provider.label} price (USD per million tokens)`} wide>
              <div className="settings-inline-actions">
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  aria-label={`${provider.label} input price`}
                  placeholder="Input"
                  value={price?.inputUsdPerMillion ?? ''}
                  onChange={(event) => setPrice('inputUsdPerMillion', event.currentTarget.value)}
                />
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  aria-label={`${provider.label} output price`}
                  placeholder="Output"
                  value={price?.outputUsdPerMillion ?? ''}
                  onChange={(event) => setPrice('outputUsdPerMillion', event.currentTarget.value)}
                />
              </div>
            </SettingsField>
          );
        })}
      </div>
    </SettingsSection>
  );
}
