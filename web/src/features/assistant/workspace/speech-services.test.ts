import { describe, expect, it } from 'vitest';
import {
  createFetchSpeechServiceTransport,
  createSttServiceClient,
  createTtsServiceClient,
  type SpeechServiceTransport,
  type SpeechServiceTransportRequest,
} from './speech-services';

describe('speech service clients', () => {
  it('uses the gateway transcription path without authority parameters or a repeated suffix', async () => {
    const requests: SpeechServiceTransportRequest[] = [];
    const transport: SpeechServiceTransport = async <TResponse>(request: SpeechServiceTransportRequest) => {
      requests.push(request);
      return { text: 'hello' } as TResponse;
    };
    for (const baseUrl of ['/api/stt?authority=auto', '/api/stt/transcribe?language=en', '/api/stt/ws/transcribe']) {
      await createSttServiceClient({ baseUrl, transport }).transcribeAudio({ audio: new ArrayBuffer(2) });
    }
    expect(requests.map((request) => request.url)).toEqual(Array(3).fill('/api/stt/transcribe'));
  });

  it('sends the browser client marker without a service credential', async () => {
    let sentHeaders: HeadersInit | undefined;
    const transport = createFetchSpeechServiceTransport(async (_url, options) => {
      sentHeaders = options?.headers;
      return new Response(JSON.stringify({ text: 'hello' }));
    });
    await transport({ url: '/api/stt/transcribe', method: 'POST', body: new FormData() });
    const headers = new Headers(sentHeaders);
    expect(headers.get('X-Omnix-Client')).toBe('web');
    expect(headers.has('X-Omnix-Service-Token')).toBe(false);
  });

  it('posts audio to the configured STT service', async () => {
    const requests: SpeechServiceTransportRequest[] = [];
    const transport: SpeechServiceTransport = async <TResponse>(request: SpeechServiceTransportRequest) => {
      requests.push(request);
      return { text: 'hello omnix', confidence: 0.91 } as TResponse;
    };
    const client = createSttServiceClient({
      baseUrl: 'http://localhost:5201/',
      transport,
    });

    const response = await client.transcribeAudio({
      audio: new ArrayBuffer(3),
      language: 'en',
      prompt: 'short command',
    });

    expect(response.text).toBe('hello omnix');
    expect(requests[0]).toMatchObject({ url: 'http://localhost:5201/transcribe', method: 'POST' });
    expect(requests[0]?.body).toBeInstanceOf(FormData);
  });

  it('posts synthesis requests to the configured TTS service', async () => {
    const requests: SpeechServiceTransportRequest[] = [];
    const transport: SpeechServiceTransport = async <TResponse>(request: SpeechServiceTransportRequest) => {
      requests.push(request);
      return { audioUrl: 'blob:voice', mimeType: 'audio/wav', durationSeconds: 1.5 } as TResponse;
    };
    const client = createTtsServiceClient({
      baseUrl: 'http://localhost:5101',
      transport,
    });

    const response = await client.synthesizeSpeech({ text: 'Welcome back.', voice: 'narrator', format: 'wav' });

    expect(response.audioUrl).toBe('blob:voice');
    expect(requests[0]).toMatchObject({
      url: 'http://localhost:5101/synthesize',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    expect(requests[0]?.body).toBe(JSON.stringify({ text: 'Welcome back.', voice: 'narrator', format: 'wav' }));
  });

  it('converts failed fetch responses into service errors', async () => {
    const transport = createFetchSpeechServiceTransport(async () => new Response('offline', { status: 503 }) as Response);

    await expect(
      transport({
        url: 'http://localhost:5101/synthesize',
        method: 'POST',
        body: JSON.stringify({ text: 'test' }),
      }),
    ).rejects.toThrow('Speech service request failed with status 503');
  });
});
