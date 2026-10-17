import { SettingsStatusRow } from './SettingsPrimitives';
import { useSettingsStatus } from './useSettingsStatus';

export function SettingsStatusRail() {
  const { status, refreshing, lastError, refresh } = useSettingsStatus();
  return (
    <aside className="settings-status-rail" aria-label="System status">
      <section className="settings-status-card">
        <h3>System status</h3>
        <SettingsStatusRow label="Gateway" value={status.gateway} tone="idle" />
        <SettingsStatusRow label="Language model" value={status.llm} tone="idle" />
        <SettingsStatusRow label="TTS" value={status.tts} tone="idle" />
        <SettingsStatusRow label="STT" value={status.stt} tone="idle" />
        <SettingsStatusRow label="Image worker" value={status.image} tone="idle" />
      </section>
      <section className="settings-status-card">
        <h3>Runtime resources</h3>
        <p>{status.loadedModels} loaded models and {status.activeJobs} active jobs.</p>
        {lastError ? <small>{lastError}</small> : null}
      </section>
      <button className="settings-run-tests" type="button" disabled={refreshing} onClick={() => void refresh()}>{refreshing ? 'Testing...' : 'Run all tests'}</button>
    </aside>
  );
}
