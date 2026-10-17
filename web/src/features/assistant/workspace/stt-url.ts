export type SpeechLocation = Pick<Location, 'protocol' | 'hostname'> & Partial<Pick<Location, 'host'>>;

export function speechOrigin(location: SpeechLocation): string {
  const hostname = location.hostname.includes(':') && !location.hostname.startsWith('[')
    ? `[${location.hostname}]` : location.hostname;
  return `${location.protocol}//${location.host || hostname}`;
}

export function streamingSttUrl(value: string, location: SpeechLocation): URL {
  const url = new URL(value.trim(), speechOrigin(location));
  const language = url.searchParams.get('language')?.trim();
  url.protocol = url.protocol === 'https:' || url.protocol === 'wss:' ? 'wss:' : 'ws:';
  const path = url.pathname.replace(/\/+$/, '');
  if (path.endsWith('/ws/transcribe')) url.pathname = path;
  else if (path.endsWith('/transcribe')) url.pathname = `${path.slice(0, -'/transcribe'.length)}/ws/transcribe`;
  else url.pathname = `${path}/ws/transcribe`.replace(/\/{2,}/g, '/');
  url.search = '';
  if (language) url.searchParams.set('language', language);
  url.hash = '';
  return url;
}
