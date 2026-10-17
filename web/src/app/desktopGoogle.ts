import { startDesktopGoogle, takeDesktopGoogleHandoff, type DesktopGoogleHandoff } from '../api/authClient';
import type { DesktopBridge } from '../shared/desktopBridge';
import { POLL_INTERVALS_MS, startPolling } from '../shared/timers';

/**
 * Google sign-in from the desktop app (MarketPulse S-5). Google refuses embedded
 * browsers, so the system browser opens the begin link and this window polls
 * with its nonce until the gateway hands it its own session.
 */
export async function desktopGoogleSignIn(
  bridge: Pick<DesktopBridge, 'openExternal'>,
  request: { next: string; invite?: string | null; remember?: boolean; link?: boolean },
  intervalMs: number = POLL_INTERVALS_MS.desktopGoogleSignIn,
): Promise<DesktopGoogleHandoff> {
  const started = await startDesktopGoogle({
    next: request.next,
    invite: request.invite ?? null,
    remember: request.remember ?? true,
    link: request.link ?? false,
  });
  bridge.openExternal(new URL(started.begin_url, window.location.origin).toString());
  const deadline = Date.now() + started.expires_in * 1000;
  return new Promise<DesktopGoogleHandoff>((resolve, reject) => {
    const stop = startPolling(async () => {
      try {
        const handed = await takeDesktopGoogleHandoff(started.nonce);
        if (handed.status !== 'pending') {
          stop();
          resolve(handed);
        } else if (Date.now() >= deadline) {
          stop();
          reject(new DOMException('Google sign-in took too long.', 'TimeoutError'));
        }
      } catch (caught) {
        stop();
        reject(caught);
      }
    }, intervalMs);
  });
}
