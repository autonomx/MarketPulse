import { isIP } from 'node:net';

export function resolveListenerHost(
  env: Record<string, string | undefined>,
  requested?: string,
): string {
  const configured = env.OMNIX_BIND_HOST?.trim() || '127.0.0.1';
  const host = requested === undefined ? configured : requested.trim();
  const family = isIP(host);
  if (host !== 'localhost' && !family) throw new Error('OMNIX_BIND_HOST must be an IP address or localhost');
  const loopback = host === 'localhost' || host === '::1'
    || (family === 4 && host.startsWith('127.'));
  if (!loopback && (host !== configured || env.OMNIX_ALLOW_LAN?.trim().toLowerCase() !== 'true')) {
    throw new Error('Non-loopback binding requires OMNIX_BIND_HOST and OMNIX_ALLOW_LAN=true');
  }
  return host;
}
