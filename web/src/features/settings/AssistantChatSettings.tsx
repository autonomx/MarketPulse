import { useEffect } from 'react';
import { syncAssistantPreferences } from './assistantPreferencesBridge';
import { AgentRunLimitsSection } from './AgentRunLimitsSection';
import { ResearchSettingsSection } from './ResearchSettingsSection';
import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';

export function AssistantChatSettings() {
  const { state, dispatch } = useSettingsProfileContext();
  const value = state.draft.assistant;
  useEffect(() => syncAssistantPreferences(value), [value]);
  return (
    <div className="settings-category-panel">
      <h2>Assistant & Chat</h2>
      <p>Defaults for new conversations, research, screen observation, and voice responses.</p>
      <SettingsSection title="Assistant defaults" scope="module">
        <div className="settings-form-grid">
          <SettingsField label="Personality">
            <select value={value.personalityId} onChange={(event) => dispatch({ type: 'update', path: 'assistant.personalityId', value: event.currentTarget.value })}>
              <option value="omnix-default">Omnix Default</option>
              <option value="concise">Concise operator</option>
              <option value="coach">Friendly coach</option>
              <option value="technical">Technical expert</option>
              <option value="creative">Creative collaborator</option>
              <option value="custom">Custom personality</option>
            </select>
          </SettingsField>
          <SettingsField label="Assistant voice">
            <input value={value.voiceId} placeholder="Runtime default" onChange={(event) => dispatch({ type: 'update', path: 'assistant.voiceId', value: event.currentTarget.value })} />
          </SettingsField>
          <SettingsField label="Default web research">
            <select
              aria-label="Default web research"
              value={value.researchDefaultMode}
              onChange={(event) => dispatch({ type: 'update', path: 'assistant.researchDefaultMode', value: event.currentTarget.value })}
            >
              <option value="disabled">Disabled</option>
              <option value="quick">Quick search</option>
              <option value="deep">Deep research</option>
            </select>
          </SettingsField>
          {value.personalityId === 'custom' ? (
            <SettingsField label="Custom personality" wide>
              <textarea rows={4} value={value.customPersonality} onChange={(event) => dispatch({ type: 'update', path: 'assistant.customPersonality', value: event.currentTarget.value })} />
            </SettingsField>
          ) : null}
        </div>
      </SettingsSection>
      <ResearchSettingsSection />
      <AgentRunLimitsSection />
      <SettingsSection title="Desktop Companion (experimental)" scope="module">
        <p>Continuous observation is opt-in. Shadow mode records redacted decisions without generating or delivering comments.</p>
        <div className="settings-form-grid">
          <SettingsField label="Rollout stage">
            <select
              aria-label="Desktop Companion rollout stage"
              value={value.desktopCompanionRolloutStage}
              onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionRolloutStage', value: event.currentTarget.value })}
            >
              <option value="disabled">Disabled</option>
              <option value="shadow">Shadow observation</option>
              <option value="text">Text comments after gate</option>
              <option value="speech">Spoken comments after gate</option>
            </select>
          </SettingsField>
          <SettingsField label="Vision model (Codex/Luna supported)">
            <input
              value={value.desktopCompanionVisionModelId}
              placeholder="Use active model or enter a model ID"
              onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionVisionModelId', value: event.currentTarget.value })}
            />
          </SettingsField>
          <SettingsField label="Background calls per minute">
            <input
              type="number"
              min={1}
              max={30}
              value={value.desktopCompanionBackgroundCallsPerMinute}
              onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionBackgroundCallsPerMinute', value: Number(event.currentTarget.value) })}
            />
          </SettingsField>
          <SettingsField label="Minimum observation interval (ms)">
            <input
              type="number"
              min={2000}
              max={120000}
              step={500}
              value={value.desktopCompanionMinimumObservationIntervalMs}
              onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionMinimumObservationIntervalMs', value: Number(event.currentTarget.value) })}
            />
          </SettingsField>
          <SettingsField label="Observation timeout (ms)">
            <input
              type="number"
              min={1000}
              max={60000}
              step={500}
              value={value.desktopCompanionObservationTimeoutMs}
              onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionObservationTimeoutMs', value: Number(event.currentTarget.value) })}
            />
          </SettingsField>
          <SettingsField label="Commentary cooldown (ms)">
            <input
              type="number"
              min={5000}
              max={300000}
              step={1000}
              value={value.desktopCompanionCommentaryCooldownMs}
              onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionCommentaryCooldownMs', value: Number(event.currentTarget.value) })}
            />
          </SettingsField>
        </div>
        <div className="settings-toggle-list">
          <label><input type="checkbox" checked={value.desktopCompanionEnabled} onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionEnabled', value: event.currentTarget.checked })} /><span>Enable Desktop Companion</span></label>
          <label><input type="checkbox" checked={value.desktopCompanionRemoteVisionAllowed} onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionRemoteVisionAllowed', value: event.currentTarget.checked })} /><span>Allow remote vision provider (required for Codex/Luna)</span></label>
          <label><input type="checkbox" checked={value.desktopCompanionShowDiagnostics} onChange={(event) => dispatch({ type: 'update', path: 'assistant.desktopCompanionShowDiagnostics', value: event.currentTarget.checked })} /><span>Show redacted companion diagnostics</span></label>
        </div>
      </SettingsSection>
      <SettingsSection title="Voice responses" scope="module">
        <div className="settings-form-grid">
          <SettingsField label="Speech language">
            <input value={value.speechLanguage} onChange={(event) => dispatch({ type: 'update', path: 'assistant.speechLanguage', value: event.currentTarget.value })} />
          </SettingsField>
        </div>
        <div className="settings-toggle-list">
          <label><input type="checkbox" checked={value.autoSpeakReplies} onChange={(event) => dispatch({ type: 'update', path: 'assistant.autoSpeakReplies', value: event.currentTarget.checked })} /><span>Auto-speak replies</span></label>
          <label><input type="checkbox" checked={value.streamingAudio} onChange={(event) => dispatch({ type: 'update', path: 'assistant.streamingAudio', value: event.currentTarget.checked })} /><span>Stream response audio</span></label>
        </div>
      </SettingsSection>
    </div>
  );
}
