import { describe, expect, it } from 'vitest';
import { resolveListenerHost } from './listener-policy';

describe('listener policy', () => {
  it('defaults to loopback', () => {
    expect(resolveListenerHost({})).toBe('127.0.0.1');
  });
  it.each(['localhost', '127.0.0.2', '::1'])('allows loopback %s', (host) => {
    expect(resolveListenerHost({}, host)).toBe(host);
  });
  it.each(['', 'true'])('rejects LAN binding without both settings (%s)', (flag) => {
    expect(() => resolveListenerHost({ OMNIX_ALLOW_LAN: flag }, '192.168.1.2')).toThrow();
  });
  it('requires the LAN flag', () => {
    expect(() => resolveListenerHost({ OMNIX_BIND_HOST: '192.168.1.2' })).toThrow();
  });
  it('accepts explicit LAN binding', () => {
    expect(resolveListenerHost({ OMNIX_BIND_HOST: '192.168.1.2', OMNIX_ALLOW_LAN: 'true' })).toBe('192.168.1.2');
  });
  it.each(['', 'example.com', '127.0.0.1:8000'])('rejects invalid addresses (%s)', (host) => {
    expect(() => resolveListenerHost({}, host)).toThrow();
  });
});
