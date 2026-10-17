import { useState } from 'react';
import type { PaperAccount, PaperAccountSnapshot } from './paperTypes';
import { tradingPaperApi } from './tradingPaperApi';
import { PRODUCT_NAME } from '../../shared/productIdentity';

type ToggleSetting = 'allow_short' | 'notify_margin_calls';

type ToggleProps = {
  mode: 'create' | 'settings';
  draftValue: boolean;
  onDraftChange: (value: boolean) => void;
  account: PaperAccount | null;
  onSaved: (snapshot: PaperAccountSnapshot) => void;
};

/**
 * A yes/no setting of a paper account. When creating an account it is part of the form; in an account's settings it is
 * saved at once, at the account's revision.
 */
function PaperAccountToggleField({
  setting, legend, label, hint, failure, mode, draftValue, onDraftChange, account, onSaved,
}: ToggleProps & { setting: ToggleSetting; legend: string; label: string; hint: string; failure: string }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checked = mode === 'create' ? draftValue : Boolean(account?.[setting]);
  const change = (next: boolean) => {
    if (mode === 'create') {
      onDraftChange(next);
      return;
    }
    if (!account) return;
    setSaving(true);
    setError(null);
    void tradingPaperApi.updateAccountSettings(account, { [setting]: next })
      .then(onSaved)
      .catch((caught: unknown) => setError(caught instanceof Error && caught.message.includes('(409)')
        ? 'The account changed elsewhere; reopen its settings and try again.'
        : failure))
      .finally(() => setSaving(false));
  };
  return (
    <fieldset>
      <legend>{legend}</legend>
      <label className="trading-account-check">
        <input type="checkbox" checked={checked} disabled={saving || (mode === 'settings' && !account)} onChange={(event) => change(event.target.checked)} />
        <span>{label}</span>
      </label>
      <small className="trading-account-hint">{hint}</small>
      {error ? <small className="trading-account-hint" role="alert">{error}</small> : null}
    </fieldset>
  );
}

/**
 * Shorting for a paper account (TVP-7.2a). When creating an account it starts on, as TradingView's paper accounts do.
 * Turning it off stops new shorts; open shorts stay.
 */
export function PaperShortingField(props: ToggleProps) {
  return (
    <PaperAccountToggleField
      {...props}
      setting="allow_short"
      legend="Shorting"
      label="Allow short positions"
      hint="A sell with no position opens a short, sized by the account's risk rule with a stop above the entry."
      failure="Shorting could not be changed."
    />
  );
}

/**
 * Margin-call notifications for a paper account (TVP-7.2b): off unless asked for. A margin call always shows in
 * Omnix; with this on it is also sent by the workspace's email and push notifications, where they are set up.
 */
export function PaperMarginCallNotifyField(props: ToggleProps) {
  return (
    <PaperAccountToggleField
      {...props}
      setting="notify_margin_calls"
      legend="Margin calls"
      label="Send margin calls by email and push"
      hint={`Uses the email and push notifications set up for alerts. Margin calls always show in ${PRODUCT_NAME}'s notifications.`}
      failure="The margin-call setting could not be changed."
    />
  );
}
