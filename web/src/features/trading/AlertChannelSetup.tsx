/**
 * Inline setup of the Email and Push alert channels (TVP-0.5b/c), shown in the alert dialog when one is chosen:
 * the workspace's SMTP server, and notifications on this device. The SMTP password is write-only.
 */
import { useEffect, useState } from 'react';
import {
  alertNotificationsApi, currentPushSubscription, enablePushNotifications, pushSupported,
  type AlertDeliveryTest, type AlertEmailState, type AlertPushState,
} from './alertNotificationsApi';
import './AlertChannelSetup.css';
import { PRODUCT_NAME } from '../../shared/productIdentity';

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
const testNote = (result: AlertDeliveryTest) => (result.outcome === 'delivered' ? 'Sent.' : `Not sent: ${result.error ?? result.outcome}.`);

type EmailDraft = { host: string; port: string; security: 'starttls' | 'tls' | 'none'; username: string; password: string; from: string; to: string };

export function AlertEmailSetup() {
  const [state, setState] = useState<AlertEmailState | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<EmailDraft>({ host: '', port: '587', security: 'starttls', username: '', password: '', from: '', to: '' });
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    alertNotificationsApi.email().then((loaded) => {
      if (!live) return;
      setState(loaded);
      const settings = loaded.settings;
      if (settings) setDraft({ host: settings.host, port: String(settings.port), security: settings.security ?? 'starttls', username: settings.username ?? '', password: '', from: settings.from_address, to: settings.to_addresses.join(', ') });
      setEditing(!loaded.configured);
    }, (error: unknown) => { if (live) setNote(message(error)); });
    return () => { live = false; };
  }, []);
  if (!state) return <p className="trading-alert-channel-setup" role="status">{note ?? 'Checking email delivery…'}</p>;
  const save = async () => {
    setNote(null);
    try {
      const saved = await alertNotificationsApi.saveEmail({
        host: draft.host, port: Number(draft.port) || 587, security: draft.security, username: draft.username,
        password: draft.password ? draft.password : null, from_address: draft.from, to_addresses: draft.to.split(/[,;\s]+/).filter(Boolean),
      });
      setState(saved);
      setEditing(false);
      setDraft((current) => ({ ...current, password: '' }));
    } catch (error) {
      setNote(message(error));
    }
  };
  const test = async () => {
    setNote('Sending a test…');
    try { setNote(testNote(await alertNotificationsApi.testEmail())); } catch (error) { setNote(message(error)); }
  };
  const set = (patch: Partial<EmailDraft>) => setDraft((current) => ({ ...current, ...patch }));
  return (
    <div className="trading-alert-channel-setup" role="group" aria-label="Email delivery">
      {!editing && state.settings ? (
        <p>
          Emails go to {state.settings.to_addresses.join(', ')} through {state.settings.host}.
          <button type="button" onClick={() => setEditing(true)}>Change</button>
          <button type="button" onClick={() => void test()}>Send test</button>
        </p>
      ) : (
        <>
          <span>Email needs an SMTP server (your mail provider's, or a relay):</span>
          <input aria-label="SMTP host" placeholder="smtp.example.com" value={draft.host} onChange={(event) => set({ host: event.target.value })} />
          <input aria-label="SMTP port" inputMode="numeric" value={draft.port} onChange={(event) => set({ port: event.target.value })} />
          <select aria-label="SMTP security" value={draft.security} onChange={(event) => set({ security: event.target.value as EmailDraft['security'] })}>
            <option value="starttls">STARTTLS</option><option value="tls">TLS</option><option value="none">None (no login)</option>
          </select>
          <input aria-label="SMTP username" placeholder="Username (optional)" autoComplete="off" value={draft.username} onChange={(event) => set({ username: event.target.value })} />
          <input aria-label="SMTP password" type="password" autoComplete="new-password" placeholder={state.settings?.has_password ? 'Password saved; type to replace' : 'Password'} value={draft.password} onChange={(event) => set({ password: event.target.value })} />
          <input aria-label="From address" type="email" placeholder="alerts@example.com" value={draft.from} onChange={(event) => set({ from: event.target.value })} />
          <input aria-label="To addresses" placeholder="you@example.com (up to 5)" value={draft.to} onChange={(event) => set({ to: event.target.value })} />
          <button type="button" onClick={() => void save()}>Save email settings</button>
          {state.configured ? <button type="button" onClick={() => setEditing(false)}>Cancel</button> : null}
          {!state.store_available ? <small>This server can't store an SMTP password; use a server without a login.</small> : null}
        </>
      )}
      {note ? <small role="status">{note}</small> : null}
    </div>
  );
}

export function AlertPushSetup() {
  const [state, setState] = useState<AlertPushState | null>(null);
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const supported = pushSupported();
  useEffect(() => {
    let live = true;
    alertNotificationsApi.push().then((loaded) => { if (live) setState(loaded); }, (error: unknown) => { if (live) setNote(message(error)); });
    currentPushSubscription().then((subscription) => { if (live) setSubscribed(subscription !== null); }, () => { if (live) setSubscribed(false); });
    return () => { live = false; };
  }, []);
  const enable = async () => {
    if (!state?.public_key) return;
    setNote(null);
    try {
      await enablePushNotifications(state.public_key);
      setSubscribed(true);
      setState(await alertNotificationsApi.push());
    } catch (error) {
      setNote(message(error));
    }
  };
  const test = async () => {
    setNote('Sending a test…');
    try { setNote(testNote(await alertNotificationsApi.testPush())); } catch (error) { setNote(message(error)); }
  };
  const devices = state?.subscriptions.length ?? 0;
  return (
    <div className="trading-alert-channel-setup" role="group" aria-label="Push delivery">
      {!supported ? <p>This browser can't show push notifications; another device can.</p>
        : state && !state.available ? <p>This server can't send push notifications (no protected credential store).</p>
          : subscribed ? (
            <p>
              This device gets alert notifications{devices > 1 ? `, with ${devices - 1} other device${devices > 2 ? 's' : ''}` : ''}.
              <button type="button" onClick={() => void test()}>Send test</button>
            </p>
          ) : (
            <p>
              Notifications reach your devices even when {PRODUCT_NAME} is closed.
              <button type="button" disabled={!state?.public_key} onClick={() => void enable()}>Allow notifications on this device</button>
            </p>
          )}
      {note ? <small role="status">{note}</small> : null}
    </div>
  );
}
