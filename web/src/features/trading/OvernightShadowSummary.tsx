import type { OvernightShadowTradingStrategyConfig, StrategyEvent } from './tradingStrategyTypes';

type ArmScore = {
  arm_id: string;
  picks: number;
  labelled: number;
  mean_net_return: string | number | null;
  median_net_return: string | number | null;
  winners: number;
  big_gap_ups: number;
  big_gap_downs: number;
};

function pct(value: unknown): string {
  const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
  return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : '—';
}

function payloadOf(event: StrategyEvent): Record<string, unknown> {
  return (event.payload ?? {}) as Record<string, unknown>;
}

/** Read-only view of the SHADOW overnight research group: schedule, arm rules, latest picks and scorecards. */
export function OvernightShadowSummary({ strategy, events }: { strategy: OvernightShadowTradingStrategyConfig; events: StrategyEvent[] }) {
  const config = strategy.config;
  const scorecards = events.filter((event) => event.event_type === 'overnight_scorecard');
  const picks = events.filter((event) => event.event_type === 'overnight_pick');
  const latestPickRun = picks.reduce<string | null>((latest, event) => (event.run_id && (!latest || event.run_id > latest) ? event.run_id : latest), null);
  const latestPicks = picks.filter((event) => event.run_id === latestPickRun);
  return (
    <div className="trading-overnight-summary">
      <header className="trading-strategy-editor-header">
        <div>
          <strong>{strategy.strategy_id}</strong>
          <small>overnight_shadow_v1 · {strategy.mode} · SHADOW research only — no orders are placed · revision {strategy.revision}</small>
        </div>
      </header>
      <p>
        Decides at {config.decision_et} ET from 30-minute SIP bars that ended by {config.data_cutoff_et}; records a shadow entry at
        the {config.entry_et} one-minute open and labels the next session after {config.label_after_et} (primary exit: 09:30 open).
      </p>
      <table>
        <thead><tr><th>Arm</th><th>Enabled</th><th>Change</th><th>Volume ÷ ADV</th><th>Close location</th></tr></thead>
        <tbody>
          {config.arms.map((arm) => (
            <tr key={arm.arm_id}>
              <td><strong>{arm.arm_id}</strong></td>
              <td>{arm.enabled ? 'yes' : 'no'}</td>
              <td>≥ {pct(arm.min_change)}{arm.max_change != null ? ` and < ${pct(arm.max_change)}` : ''}</td>
              <td>≥ {String(arm.min_volume_ratio)}×</td>
              <td>≥ {String(arm.min_close_location)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Latest picks {latestPickRun ? `(${latestPickRun.replace('overnight:', '')})` : ''}</h4>
      {latestPicks.length ? (
        <ul>
          {latestPicks.map((event) => {
            const payload = payloadOf(event);
            const candidate = (payload.candidate ?? {}) as Record<string, unknown>;
            return <li key={event.event_id}>{String(payload.arm_id)} · {String(candidate.symbol)} · {pct(candidate.change)} today</li>;
          })}
        </ul>
      ) : <p>No picks recorded yet.</p>}
      <h4>Scorecards</h4>
      {scorecards.length ? (
        <table>
          <thead><tr><th>Entry session</th><th>Arm</th><th>Picks</th><th>Mean net</th><th>Median net</th><th>Winners</th><th>≥ +20% gaps</th><th>≤ −20% gaps</th></tr></thead>
          <tbody>
            {scorecards.flatMap((event) => {
              const payload = payloadOf(event);
              const rows = [...((payload.arms ?? []) as ArmScore[]), payload.population_10pct as ArmScore].filter(Boolean);
              return rows.map((row) => (
                <tr key={`${event.event_id}-${row.arm_id}`}>
                  <td>{String(payload.entry_session)}</td><td>{row.arm_id}</td><td>{row.picks}</td>
                  <td>{pct(row.mean_net_return)}</td><td>{pct(row.median_net_return)}</td><td>{row.winners}</td>
                  <td>{row.big_gap_ups}</td><td>{row.big_gap_downs}</td>
                </tr>
              ));
            })}
          </tbody>
        </table>
      ) : <p>No labelled sessions yet; the first scorecard appears the morning after the first scan.</p>}
    </div>
  );
}
