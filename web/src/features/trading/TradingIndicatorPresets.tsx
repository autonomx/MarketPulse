import { useEffect, useState } from 'react';
import type { CoreIndicatorInstance } from './indicators/coreIndicators';
import { tradingApi } from './tradingApi';
import type { TradingDocument } from './tradingTypes';

function presetIndicators(record: TradingDocument): CoreIndicatorInstance[] {
  const payload = record.payload as { indicators?: CoreIndicatorInstance[] };
  return Array.isArray(payload.indicators)
    ? payload.indicators.filter((item) => item && typeof item.id === 'string' && typeof item.period === 'number')
    : [];
}

export function TradingIndicatorPresets({
  indicators,
  onApply,
}: {
  indicators: CoreIndicatorInstance[];
  onApply: (indicators: CoreIndicatorInstance[]) => void;
}) {
  const [records, setRecords] = useState<TradingDocument[]>([]);
  const [name, setName] = useState('Analysis preset');
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');

  const refresh = () => tradingApi.documents('indicator-presets').then((items) => {
    // Chart templates (TVP-2.5) share these documents; they are listed in chart settings instead.
    setRecords(items.filter((item) => item.status === 'active' && !('templateKind' in item.payload)));
    setStatus('ready');
  }).catch(() => setStatus('error'));

  useEffect(() => { void refresh(); }, []);

  const save = async () => {
    setStatus('saving');
    const id = `preset-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'analysis'}-${Date.now()}`;
    try {
      await tradingApi.createDocument('indicator-presets', id, {
        name,
        formulaVersion: 'omnix-indicators-v2',
        indicators: indicators.map((item) => ({ ...item })),
      });
      await refresh();
    } catch {
      setStatus('error');
    }
  };

  const archive = async (record: TradingDocument) => {
    setStatus('saving');
    try {
      await tradingApi.archiveDocument('indicator-presets', record);
      await refresh();
    } catch {
      setStatus('error');
    }
  };

  return (
    <section className="trading-indicator-presets" aria-label="Indicator presets" data-status={status}>
      <header><strong>Indicator presets</strong><span>{status}</span></header>
      <div className="trading-advanced-controls">
        <label>Preset name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <button type="button" onClick={() => void save()}>Save current</button>
      </div>
      <ul>
        {records.map((record) => (
          <li key={record.record_id}>
            <button type="button" onClick={() => onApply(presetIndicators(record))}>
              {(record.payload.name as string | undefined) ?? record.record_id}
            </button>
            <button type="button" aria-label={`Archive ${record.record_id}`} onClick={() => void archive(record)}>×</button>
          </li>
        ))}
      </ul>
    </section>
  );
}
