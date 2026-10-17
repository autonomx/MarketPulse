export async function resolveLiveVoiceDeviceKey(stream: MediaStream): Promise<string> {
  const input = stream.getAudioTracks()[0]?.getSettings().deviceId || 'default-input';
  let output = 'default-output';
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    output = devices.find((device) => device.kind === 'audiooutput')?.deviceId || output;
  } catch {
    // Device enumeration is optional; the fallback pair remains deterministic.
  }
  return stableLiveVoiceDeviceHash(`${input}|${output}`);
}

export async function stableLiveVoiceDeviceHash(value: string): Promise<string> {
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `fallback-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}
