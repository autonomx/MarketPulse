import { useEffect, useState } from 'react';
import { omnixApiClient } from '../../api/client';
import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';

export type OperationsSettingsView = 'storage' | 'runtime';
export type OperationsSnapshot = { jobs: number; assets: number; reports: number; models: number; diagnostics: Record<string, unknown> };

function countField(value: unknown, key: string): number {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return 0;
  const rows = (value as Record<string, unknown>)[key];
  return Array.isArray(rows) ? rows.length : 0;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return 'Not reported';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return `${value.length} items`;
  return `${Object.keys(value as Record<string, unknown>).length} fields`;
}

export function OperationsSettings({ view = 'storage' }: { view?: OperationsSettingsView }) {
  const { state, dispatch } = useSettingsProfileContext();
  const storage = state.draft.storage;
  const [snapshot, setSnapshot] = useState<OperationsSnapshot>({ jobs: 0, assets: 0, reports: 0, models: 0, diagnostics: {} });
  useEffect(() => {
    let active = true;
    Promise.allSettled([omnixApiClient.listJobs(), omnixApiClient.listAssets(), omnixApiClient.listReports(), omnixApiClient.getModelResidency(), omnixApiClient.getDiagnostics()]).then(([jobs, assets, reports, models, diagnostics]) => {
      if (!active) return;
      setSnapshot({ jobs: jobs.status === 'fulfilled' ? countField(jobs.value, 'jobs') : 0, assets: assets.status === 'fulfilled' ? countField(assets.value, 'assets') : 0, reports: reports.status === 'fulfilled' ? countField(reports.value, 'reports') : 0, models: models.status === 'fulfilled' ? countField(models.value, 'records') : 0, diagnostics: diagnostics.status === 'fulfilled' ? diagnostics.value as unknown as Record<string, unknown> : {} });
    });
    return () => { active = false; };
  }, []);
  if (view === 'runtime') {
    const entries = Object.entries(snapshot.diagnostics).slice(0, 12);
    return <div><h2>Runtime details</h2><SettingsSection title="System summary" scope="status"><div className="settings-detail-grid">{entries.length ? entries.map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{displayValue(value)}</dd></div>) : <p>No runtime details reported.</p>}</div></SettingsSection></div>;
  }
  return <div><h2>Jobs, Assets & Storage</h2><SettingsSection title="Output defaults" scope="global"><SettingsField label="Retention days"><input type="number" min="1" max="3650" value={storage.retentionDays} onChange={(event) => dispatch({ type: 'update', path: 'storage.retentionDays', value: Number(event.currentTarget.value) })} /></SettingsField><div className="settings-toggle-list"><label><input type="checkbox" checked={storage.saveOutputByDefault} onChange={(event) => dispatch({ type: 'update', path: 'storage.saveOutputByDefault', value: event.currentTarget.checked })} /><span>Store new outputs</span></label><label><input type="checkbox" checked={storage.temporaryAssetCleanup} onChange={(event) => dispatch({ type: 'update', path: 'storage.temporaryAssetCleanup', value: event.currentTarget.checked })} /><span>Temporary record maintenance</span></label></div></SettingsSection><SettingsSection title="Current totals" scope="status"><p>{snapshot.jobs} jobs, {snapshot.assets} assets, {snapshot.reports} reports, {snapshot.models} loaded models</p></SettingsSection></div>;
}
