import { pipelineFetch } from '../../../api/fetchPipeline';
export type SpeechServiceTransportRequest = {
  url: string;
  method: 'POST';
  headers?: Record<string, string>;
  body?: BodyInit;
};

export type SpeechServiceTransport = <TResponse>(request: SpeechServiceTransportRequest) => Promise<TResponse>;

export type SpeechAudioInput = Blob | ArrayBuffer;

export type SttTranscriptionRequest = {
  audio: SpeechAudioInput;
  filename?: string;
  mimeType?: string;
  language?: string;
  prompt?: string;
};

export type SttTranscriptionResponse = {
  text: string;
  language?: string;
  confidence?: number;
  segments?: Array<{
    text: string;
    startSeconds?: number;
    endSeconds?: number;
    confidence?: number;
  }>;
};

export type TtsSynthesisRequest = {
  text: string;
  voice?: string;
  format?: 'wav' | 'mp3' | 'ogg';
  speed?: number;
  metadata?: Record<string, unknown>;
};

export type TtsSynthesisResponse = {
  audioUrl?: string;
  audioBase64?: string;
  mimeType?: string;
  durationSeconds?: number;
};

export type SpeechServiceClientOptions = {
  baseUrl: string;
  transport: SpeechServiceTransport;
};

export type SttServiceClient = {
  transcribeAudio(request: SttTranscriptionRequest): Promise<SttTranscriptionResponse>;
};

export type TtsServiceClient = {
  synthesizeSpeech(request: TtsSynthesisRequest): Promise<TtsSynthesisResponse>;
};

export function createSttServiceClient(options: SpeechServiceClientOptions): SttServiceClient {
  const baseUrl = normalizeBaseUrl(options.baseUrl).split(/[?#]/)[0]
    .replace(/\/(?:ws\/)?transcribe$/, '').replace(/\/+$/, '');

  return {
    transcribeAudio(request) {
      return options.transport<SttTranscriptionResponse>({
        url: `${baseUrl}/transcribe`,
        method: 'POST',
        body: createSttFormData(request),
      });
    },
  };
}

export function createTtsServiceClient(options: SpeechServiceClientOptions): TtsServiceClient {
  const baseUrl = normalizeBaseUrl(options.baseUrl);

  return {
    synthesizeSpeech(request) {
      return options.transport<TtsSynthesisResponse>({
        url: `${baseUrl}/synthesize`,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });
    },
  };
}

export function createFetchSpeechServiceTransport(fetchImpl: typeof fetch = pipelineFetch): SpeechServiceTransport {
  return async function fetchSpeechServiceTransport<TResponse>(request: SpeechServiceTransportRequest): Promise<TResponse> {
    const response = await fetchImpl(request.url, {
      method: request.method,
      headers: { ...request.headers, 'X-Omnix-Client': 'web' },
      body: request.body,
    });

    if (!response.ok) {
      throw new Error(`Speech service request failed with status ${response.status}`);
    }

    return (await response.json()) as TResponse;
  };
}

function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim();

  if (!trimmed) {
    throw new Error('Speech service baseUrl is required');
  }

  return trimmed.replace(/\/+$/, '');
}

function createSttFormData(request: SttTranscriptionRequest): FormData {
  const formData = new FormData();
  const filename = request.filename ?? 'audio.webm';
  const audio = request.audio instanceof Blob ? request.audio : new Blob([request.audio], { type: request.mimeType ?? 'application/octet-stream' });

  formData.append('audio', audio, filename);

  if (request.language) {
    formData.append('language', request.language);
  }

  if (request.prompt) {
    formData.append('prompt', request.prompt);
  }

  return formData;
}
