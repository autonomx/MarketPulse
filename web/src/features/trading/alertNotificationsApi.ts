/**
 * Alert email and push setup (TVP-0.5b/c): the server's settings API, and this browser's push subscription.
 * Push needs a service worker (`/omnix-push-sw.js`), the user's permission and Omnix's VAPID key.
 */
import { unwrapLabelled } from '../../api/http';
import { isDesktopApp } from '../../shared/desktopBridge';
import type { components } from './api/generated';
import { api } from './api/gateway';

const notifications = <T>(call: Promise<{ data?: T; error?: unknown; response: Response }>) => unwrapLabelled(call, 'Alert delivery');

export type AlertEmailSettings = components['schemas']['EmailSettings'];
export type AlertEmailSettingsWrite = components['schemas']['EmailSettingsWrite'];
export type AlertEmailState = components['schemas']['EmailSettingsResponse'];
export type AlertPushState = components['schemas']['PushSettingsResponse'];
export type AlertDeliveryTest = components['schemas']['DeliveryTestResponse'];

export const alertNotificationsApi = {
  email: (): Promise<AlertEmailState> => notifications(api.GET('/api/trading/notifications/email', { cache: 'no-store' })),
  saveEmail: (settings: AlertEmailSettingsWrite): Promise<AlertEmailState> => notifications(api.PUT('/api/trading/notifications/email', { body: settings })),
  removeEmail: async (): Promise<void> => { await notifications(api.DELETE('/api/trading/notifications/email')); },
  testEmail: (): Promise<AlertDeliveryTest> => notifications(api.POST('/api/trading/notifications/email/test')),
  push: (): Promise<AlertPushState> => notifications(api.GET('/api/trading/notifications/push', { cache: 'no-store' })),
  subscribe: (body: components['schemas']['PushSubscriptionWrite']) => notifications(api.POST('/api/trading/notifications/push/subscriptions', { body })),
  unsubscribe: async (subscriptionId: string): Promise<void> => {
    await notifications(api.DELETE('/api/trading/notifications/push/subscriptions/{subscription_id}', { params: { path: { subscription_id: subscriptionId } } }));
  },
  testPush: (): Promise<AlertDeliveryTest> => notifications(api.POST('/api/trading/notifications/push/test')),
};

export const PUSH_WORKER_URL = '/omnix-push-sw.js';

/** Whether this browser can receive push notifications at all; the desktop app uses its own notifications instead. */
export function pushSupported(): boolean {
  if (isDesktopApp()) return false;
  return typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

function applicationServerKey(key: string): Uint8Array<ArrayBuffer> {
  const padded = key.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (key.length % 4)) % 4);
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

/** This browser's current push subscription, if it has one. */
export async function currentPushSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration(PUSH_WORKER_URL);
  return registration ? registration.pushManager.getSubscription() : null;
}

/**
 * Asks for permission, registers the service worker and subscribes this browser with Omnix's key, then stores the
 * subscription on the server. Throws with a reason the user can act on.
 */
export async function enablePushNotifications(publicKey: string): Promise<void> {
  if (!pushSupported()) throw new Error('This browser cannot show push notifications.');
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('Notifications are blocked for this site; allow them in the browser’s site settings.');
  const registration = await navigator.serviceWorker.register(PUSH_WORKER_URL);
  await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription()
    ?? await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: applicationServerKey(publicKey) });
  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) throw new Error('The browser returned an incomplete subscription.');
  await alertNotificationsApi.subscribe({ endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth }, user_agent: navigator.userAgent.slice(0, 300) });
}
