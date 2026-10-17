import { useQuery } from '@tanstack/react-query';
import { tradingApi } from './tradingApi';
import { PRODUCT_NAME } from '../../shared/productIdentity';

function DiagnosticValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>not reported</span>;
  if (typeof value === 'object') return <code>{JSON.stringify(value)}</code>;
  return <span>{String(value)}</span>;
}

export function TradingDiagnosticsPanel() {
  const diagnostics = useQuery({
    queryKey: ['trading', 'diagnostics'],
    queryFn: tradingApi.diagnostics,
    refetchInterval: 10_000,
  });
  const entries = Object.entries(diagnostics.data?.diagnostics ?? {});
  return (
    <section className="trading-diagnostics-panel" aria-label="Trading diagnostics" aria-live="polite">
      <header>
        <strong>Runtime diagnostics</strong>
        <span>{diagnostics.isLoading ? 'loading' : diagnostics.isError ? 'error' : diagnostics.data?.ok ? 'healthy' : 'degraded'}</span>
      </header>
      <p>Provider, cache, stream, and gateway health reported by the shared {PRODUCT_NAME} Trading service.</p>
      <dl>
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{key.replaceAll('_', ' ')}</dt>
            <dd><DiagnosticValue value={value} /></dd>
          </div>
        ))}
      </dl>
      {!entries.length && !diagnostics.isLoading ? <p>No diagnostic fields were returned.</p> : null}
      <button type="button" onClick={() => void diagnostics.refetch()} disabled={diagnostics.isFetching}>
        Refresh diagnostics
      </button>
    </section>
  );
}
