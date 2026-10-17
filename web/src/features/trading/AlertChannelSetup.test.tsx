import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  alertNotificationsApi: {
    email: vi.fn(),
    saveEmail: vi.fn(),
    testEmail: vi.fn(async () => ({ outcome: 'delivered', error: null })),
    push: vi.fn(),
    testPush: vi.fn(async () => ({ outcome: 'failed', error: 'no_push_subscriptions' })),
  },
  pushSupported: vi.fn(() => true),
  currentPushSubscription: vi.fn(async () => null),
  enablePushNotifications: vi.fn(async () => undefined),
}));
vi.mock('./alertNotificationsApi', () => api);

const { AlertEmailSetup, AlertPushSetup } = await import('./AlertChannelSetup');
const { AlertDeliveryFields } = await import('./AlertDeliveryFields');

describe('email and push alert channels (TVP-0.5b/c)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.pushSupported.mockReturnValue(true);
  });

  it('offers Email and Push with their setup inline', async () => {
    api.alertNotificationsApi.email.mockResolvedValue({ configured: false, settings: null, store_available: true });
    api.alertNotificationsApi.push.mockResolvedValue({ available: true, public_key: 'BKEY', subscriptions: [] });
    render(<AlertDeliveryFields editor={{ notifications: ['app', 'email', 'push'] }} onChange={vi.fn()} />);
    expect(screen.getByRole('checkbox', { name: 'Email' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Push' })).toBeChecked();
    expect(await screen.findByRole('group', { name: 'Email delivery' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Allow notifications on this device' })).toBeEnabled();
  });

  it('saves the SMTP server without showing the password, then sends a test', async () => {
    api.alertNotificationsApi.email.mockResolvedValue({ configured: false, settings: null, store_available: true });
    const saved = { host: 'smtp.example.com', port: 465, security: 'tls', username: 'me', from_address: 'me@example.com', to_addresses: ['a@example.com', 'b@example.com'], has_password: true };
    api.alertNotificationsApi.saveEmail.mockResolvedValue({ configured: true, settings: saved, store_available: true });
    render(<AlertEmailSetup />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'SMTP host' }), { target: { value: 'smtp.example.com' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'SMTP port' }), { target: { value: '465' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'SMTP security' }), { target: { value: 'tls' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'SMTP username' }), { target: { value: 'me' } });
    fireEvent.change(screen.getByLabelText('SMTP password'), { target: { value: 'pw' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'From address' }), { target: { value: 'me@example.com' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'To addresses' }), { target: { value: 'a@example.com, b@example.com' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save email settings' })));
    expect(api.alertNotificationsApi.saveEmail).toHaveBeenCalledWith({
      host: 'smtp.example.com', port: 465, security: 'tls', username: 'me', password: 'pw', from_address: 'me@example.com',
      to_addresses: ['a@example.com', 'b@example.com'],
    });
    expect(screen.getByRole('group', { name: 'Email delivery' })).toHaveTextContent('Emails go to a@example.com, b@example.com through smtp.example.com.');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Send test' })));
    expect(screen.getByRole('status')).toHaveTextContent('Sent.');
  });

  it('subscribes this device and reports a test that found nothing to send to', async () => {
    api.alertNotificationsApi.push.mockResolvedValue({ available: true, public_key: 'BKEY', subscriptions: [] });
    render(<AlertPushSetup />);
    const allow = await screen.findByRole('button', { name: 'Allow notifications on this device' });
    await waitFor(() => expect(allow).toBeEnabled());
    await act(async () => fireEvent.click(allow));
    expect(api.enablePushNotifications).toHaveBeenCalledWith('BKEY');
    await waitFor(() => expect(screen.getByRole('group', { name: 'Push delivery' })).toHaveTextContent('This device gets alert notifications'));
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Send test' })));
    expect(screen.getByRole('status')).toHaveTextContent('Not sent: no_push_subscriptions.');
  });

  it('says when this browser cannot get push notifications', async () => {
    api.pushSupported.mockReturnValue(false);
    api.alertNotificationsApi.push.mockResolvedValue({ available: true, public_key: 'BKEY', subscriptions: [] });
    render(<AlertPushSetup />);
    expect(screen.getByRole('group', { name: 'Push delivery' })).toHaveTextContent("This browser can't show push notifications");
  });
});
