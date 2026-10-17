// Presentational parts of the paper trading ticket: the order fields for the
// advanced order types, the funds and risk figures, the exits, and the
// account's positions and open orders. TradingPaperPanel owns their state.
import type { PaperAccountSnapshot, PaperOrderType, PaperRiskPreview, PaperTimeInForce } from './paperTypes';
import { paperOrderTerms, paperOrderTypeLabel, timeInForceLabels } from './paperOrderTypes';

export function displaySymbol(instrumentId: string): string {
  const raw = instrumentId.split(':').at(-1) ?? instrumentId;
  return raw.replace('-', '/');
}

export function number(value?: string | null, digits = 2): string {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString(undefined, { maximumFractionDigits: digits })
    : '—';
}

type TrailUnit = 'amount' | 'percent';

/** The stop-limit's limit price, a trailing stop's distance, and time in force with a good-till-date expiry. */
export function PaperAdvancedOrderFields({
  orderType, quotePrice, limitPrice, setLimitPrice, trailValue, setTrailValue, trailUnit, setTrailUnit,
  timeInForceEnabled, timeInForce, setTimeInForce, expiresAt, setExpiresAt,
}: {
  orderType: PaperOrderType;
  quotePrice: string;
  limitPrice: string;
  setLimitPrice: (value: string) => void;
  trailValue: string;
  setTrailValue: (value: string) => void;
  trailUnit: TrailUnit;
  setTrailUnit: (value: TrailUnit) => void;
  timeInForceEnabled: boolean;
  timeInForce: PaperTimeInForce;
  setTimeInForce: (value: PaperTimeInForce) => void;
  expiresAt: string;
  setExpiresAt: (value: string) => void;
}) {
  return (
    <>
      {orderType === 'stop_limit' ? (
        <label className="trading-paper-price-field">
          Limit price
          <input aria-label="Limit price" inputMode="decimal" value={limitPrice} onChange={(event) => setLimitPrice(event.target.value)} placeholder={quotePrice} />
        </label>
      ) : null}

      {orderType === 'trailing_stop' ? (
        <div className="trading-paper-field-pair">
          <label className="trading-paper-price-field">
            {trailUnit === 'percent' ? 'Trail, %' : 'Trail, price'}
            <input aria-label="Trail distance" inputMode="decimal" value={trailValue} onChange={(event) => setTrailValue(event.target.value)} />
          </label>
          <label className="trading-paper-price-field">
            Trail by
            <select aria-label="Trail unit" value={trailUnit} onChange={(event) => setTrailUnit(event.target.value as TrailUnit)}>
              <option value="amount">Price</option>
              <option value="percent">Percent</option>
            </select>
          </label>
        </div>
      ) : null}

      {timeInForceEnabled ? (
        <div className="trading-paper-field-pair">
          <label className="trading-paper-price-field">
            Time in force
            <select aria-label="Time in force" value={timeInForce} onChange={(event) => setTimeInForce(event.target.value as PaperTimeInForce)}>
              <option value="gtc">{timeInForceLabels.gtc} · until cancelled</option>
              <option value="day">{timeInForceLabels.day} · session close</option>
              <option value="gtd">{timeInForceLabels.gtd} · until a date</option>
            </select>
          </label>
          {timeInForce === 'gtd' ? (
            <label className="trading-paper-price-field">
              Expires
              <input aria-label="Order expiry" type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} />
            </label>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

/** The order's value and the account's funds; for a risk-managed entry, the server's risk preview. */
export function PaperOrderMetrics({ tradeValue, availableFunds, reservedFunds, currency, riskManagedEntry, riskPreview }: {
  tradeValue: number | null;
  availableFunds: string | null | undefined;
  reservedFunds: string | null | undefined;
  currency: string;
  riskManagedEntry: boolean;
  riskPreview: PaperRiskPreview | null;
}) {
  return (
    <dl className="trading-paper-metrics">
      <div><dt>Trade value</dt><dd>{tradeValue === null ? '—' : `${number(String(tradeValue))} ${currency}`}</dd></div>
      <div><dt>Available funds</dt><dd>{availableFunds == null ? '—' : `${number(availableFunds)} ${currency}`}</dd></div>
      <div><dt>Reserved funds</dt><dd>{reservedFunds == null ? '—' : `${number(reservedFunds)} ${currency}`}</dd></div>
      {riskManagedEntry ? <div><dt>Risk at stop</dt><dd>{riskPreview ? `${number(riskPreview.actual_risk_dollars)} ${currency} · ${number(riskPreview.actual_risk_pct, 3)}%` : '—'}</dd></div> : null}
      {riskManagedEntry ? <div><dt>Open risk</dt><dd>{riskPreview ? `${number(riskPreview.aggregate_open_risk_dollars)} ${currency} · ${number(riskPreview.aggregate_open_risk_pct, 3)}%` : '—'}</dd></div> : null}
      {riskManagedEntry ? <div><dt>Buying power after</dt><dd>{riskPreview ? `${number(riskPreview.buying_power_after)} ${currency}` : '—'}</dd></div> : null}
      {riskManagedEntry ? <div><dt>Execution check</dt><dd>{riskPreview ? `${riskPreview.execution_eligible ? 'Eligible' : 'Blocked'} · ${riskPreview.spread_bps == null ? 'spread —' : `${number(riskPreview.spread_bps)} bps`} · ${riskPreview.freshness_mode}` : 'Awaiting server preview'}</dd></div> : null}
    </dl>
  );
}

/** The account's open positions and orders, and resetting or archiving it (not during replay). */
export function PaperAccountActivity({ snapshot, replayMode, onReset, onArchive }: {
  snapshot: PaperAccountSnapshot;
  replayMode: boolean;
  onReset: () => void;
  onArchive: () => void;
}) {
  const openPositions = snapshot.positions.filter((positionItem) => Number(positionItem.quantity) !== 0);
  return (
    <div className="trading-paper-activity">
      <details>
        <summary>Positions <span>{openPositions.length}</span></summary>
        <ul className="trading-paper-list">
          {openPositions.map((positionItem) => (
            <li key={positionItem.instrument_id}><strong>{displaySymbol(positionItem.instrument_id)}</strong><span>{positionItem.quantity} @ {positionItem.average_cost}</span></li>
          ))}
          {openPositions.length === 0 ? <li className="empty">No open positions.</li> : null}
        </ul>
      </details>
      <details>
        <summary>Open orders <span>{snapshot.open_orders.length}</span></summary>
        <ul className="trading-paper-list">
          {snapshot.open_orders.map((order) => (
            <li key={order.order_id}>
              <strong>{order.side} {order.quantity} · {paperOrderTypeLabel(order.order_type)}</strong>
              <span>{displaySymbol(order.instrument_id)} · {paperOrderTerms(order)}</span>
              <span>{order.order_type === 'trailing_stop' && order.stop_price ? `Stop ${number(order.stop_price)}` : 'Awaiting fill'}</span>
            </li>
          ))}
          {snapshot.open_orders.length === 0 ? <li className="empty">No open orders.</li> : null}
        </ul>
      </details>
      <details>
        <summary>Account actions</summary>
        <div className="trading-paper-danger-actions">
          <button type="button" disabled={replayMode} onClick={onReset}>Reset</button>
          <button type="button" disabled={replayMode || !snapshot.account.enabled} onClick={onArchive}>Archive</button>
        </div>
      </details>
    </div>
  );
}
