import { useEffect, useMemo, useRef, useState } from 'react';
import { omnixApiClient, type AssetListResponse, type JobRecord } from '../../api/client';
import { assignmentRowsFromSegments, mapStoryToAudioSegments, speakerRowsFromSegments, type StoryAudioScriptSegment } from './storyAudioMapper';
import { jobProgressPercent } from '../../api/jobProgress';
import { isFallbackOutputRef, jobOutputRefs } from '../../api/schemas/streams';
import { storyAudioStreamControlMessageSchema } from './storyAudioMessages';
import { parseJson } from '../../api/schemas/streams';
import { normalizeStoryAudioText, useStorySnapshot } from './storySnapshotStore';
import { downloadUrl } from '../../shared/download';

export type StoryAudioVoiceOption = { id: string; label: string };
export type StoryAudioSegment = StoryAudioScriptSegment & { title?: string };

type StoryAudioStatus = 'ready' | 'loading_voices' | 'queued' | 'running' | 'completed' | 'failed';
export type StoryAudioStreamControlMessage =
  | { type: 'start'; total_segments?: number }
  | { type: 'segment'; index?: number; speaker?: string; text?: string }
  | { type: 'done'; job_id?: string }
  | { type: 'stopped' }
  | { type: 'error'; message?: string; error?: string };
type StoryAudioRealtimeResult = { audioUrl: string; chunkCount: number };
type StoryAudioRealtimeCallbacks = { audioElement: HTMLAudioElement | null; signal: AbortSignal; onProgress: (progress: number) => void; onStatusMessage: (message: string) => void };
type StoryAudioWebSocketPayload = {
  type: 'start';
  segments: Array<{ index: number; speaker: string; text: string; voice_id: string | null; character_id: string; block_id: string; chapter_id: string }>;
  voice_mapping: Record<string, string>;
  voice_map: Record<string, string>;
  default_voices: { narrator: string | null; male: string | null; female: string | null };
  job_id: string;
};

const STORY_AUDIO_SELECTED_VOICE_KEY = 'omnix.storyteller.audio.selectedVoiceId';
const STORY_AUDIO_WS_URL_KEY = 'omnix.storyteller.audio.websocketUrl';
const STORY_AUDIO_POLL_INTERVAL_MS = 1_500;
const STORY_AUDIO_MAX_POLLS = 160;
const STORY_AUDIO_STREAM_SAMPLE_RATE = 24_000;
const STORY_AUDIO_WS_CONNECT_TIMEOUT_MS = 4_000;
const STORY_AUDIO_DEV_API_PORT = '8000';
const STORY_AUDIO_LIVE_REFRESH_MS = 700;
const STORY_AUDIO_LIVE_PAD_SECONDS = 8;
const STORY_AUDIO_INITIAL_PREROLL_SECONDS = 1.5;
const STORY_AUDIO_STARTUP_REFRESH_HOLD_SECONDS = 0.75;

export function StoryAudioPanel() {
  const [voices, setVoices] = useState<StoryAudioVoiceOption[]>([]);
  const [selectedVoiceId, setSelectedVoiceId] = useState(() => readSelectedVoiceId());
  const [status, setStatus] = useState<StoryAudioStatus>('ready');
  const [statusMessage, setStatusMessage] = useState('Ready to narrate the full story.');
  const [progress, setProgress] = useState(0);
  const [jobId, setJobId] = useState('');
  const [audioSource, setAudioSource] = useState('');
  const [filename, setFilename] = useState('story-audio.wav');
  const storySnapshot = useStorySnapshot();
  const previousFingerprint = useRef(storySnapshot.fingerprint);
  const [debugAudioJson, setDebugAudioJson] = useState('');
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const generationRunRef = useRef(0);
  const streamAbortRef = useRef<(() => void) | null>(null);

  const isGenerating = status === 'queued' || status === 'running';
  const canGenerate = Boolean(storySnapshot.text.trim()) && !isGenerating;
  const selectedVoiceLabel = useMemo(() => voiceLabelForId(selectedVoiceId, voices), [selectedVoiceId, voices]);

  useEffect(() => {
    let active = true;
    setStatus('loading_voices');
    setStatusMessage('Loading cloned voices…');
    omnixApiClient.listAssets()
      .then((payload) => {
        if (!active) return;
        const nextVoices = voiceOptionsFromAssets(payload.assets);
        setVoices(nextVoices);
        setSelectedVoiceId((current) => {
          const fallback = current || nextVoices[0]?.id || '';
          persistSelectedVoiceId(fallback);
          return fallback;
        });
        setStatus('ready');
        setStatusMessage(nextVoices.length ? 'Ready to narrate with a cloned voice.' : 'Ready to narrate with the default Voice Studio voice.');
      })
      .catch((error) => {
        if (!active) return;
        setStatus('ready');
        setStatusMessage(error instanceof Error ? error.message : 'Unable to load cloned voices.');
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (previousFingerprint.current === storySnapshot.fingerprint) return;
    previousFingerprint.current = storySnapshot.fingerprint;
    streamAbortRef.current?.();
    streamAbortRef.current = null;
    setAudioSource('');
    setProgress(0);
    setJobId('');
    setDebugAudioJson('');
    setFilename(`${slugify(storySnapshot.title || 'story')}-audio.wav`);
    setStatus('ready');
    setStatusMessage('Story changed. Ready to regenerate full-story narration.');
  }, [storySnapshot.fingerprint, storySnapshot.title]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audioSource && audio.src !== audioSource) {
      audio.srcObject = null;
      audio.src = audioSource;
      audio.load();
    }
    if (!audioSource && audio.hasAttribute('src') && !audio.srcObject) {
      audio.removeAttribute('src');
      audio.load();
    }
  }, [audioSource]);

  useEffect(() => () => {
    streamAbortRef.current?.();
    streamAbortRef.current = null;
  }, []);

  function handleVoiceChange(value: string): void {
    setSelectedVoiceId(value);
    persistSelectedVoiceId(value);
    setStatusMessage(value ? `Selected ${voiceLabelForId(value, voices) || 'cloned voice'} for narration.` : 'Using the default Voice Studio voice.');
  }

  function stopStoryAudio(): void {
    generationRunRef.current += 1;
    streamAbortRef.current?.();
    streamAbortRef.current = null;
    audioRef.current?.pause();
    setProgress(0);
    setStatus('ready');
    setStatusMessage('Story audio generation stopped.');
  }

  function printAudioDebugJson(): void {
    if (!debugAudioJson) return;
    logStoryAudioDebugPayload(debugAudioJson);
    setStatusMessage('Printed Story Audio JSON to the browser console.');
  }

  async function copyAudioDebugJson(): Promise<void> {
    if (!debugAudioJson) return;
    const copied = await copyStoryAudioDebugPayload(debugAudioJson);
    setStatusMessage(copied ? 'Copied Story Audio JSON to clipboard.' : 'Clipboard unavailable. Printed Story Audio JSON to the browser console.');
  }

  async function generateStoryAudio(): Promise<void> {
    const snapshot = storySnapshot;
    const audioMap = mapStoryToAudioSegments(snapshot.title, snapshot.text, selectedVoiceId);
    const segments = audioMap.segments;
    if (!snapshot.text.trim() || !segments.length) {
      setStatus('failed');
      setStatusMessage('Generate or select a story before creating audio.');
      return;
    }

    const runId = generationRunRef.current + 1;
    const websocketPayload = buildStoryAudioWebSocketPayload(segments, selectedVoiceId, `story-${Date.now()}`);
    generationRunRef.current = runId;
    streamAbortRef.current?.();
    const streamAbortController = new AbortController();
    streamAbortRef.current = () => streamAbortController.abort();
    setAudioSource('');
    setFilename(`${slugify(snapshot.title || 'story')}-audio.wav`);
    setProgress(2);
    setJobId(websocketPayload.job_id);
    setDebugAudioJson(JSON.stringify(websocketPayload, null, 2));
    setStatus('queued');
    setStatusMessage(`Starting realtime narration for ${segments.length > 1 ? `${segments.length} mapped story segments` : 'the full story'}…`);

    try {
      try {
        const realtimeAudio = await streamStoryAudioViaWebSocket(websocketPayload, {
          audioElement: audioRef.current,
          signal: streamAbortController.signal,
          onProgress: setProgress,
          onStatusMessage: setStatusMessage,
        });
        if (generationRunRef.current !== runId) return;
        streamAbortRef.current = null;
        setAudioSource(realtimeAudio.audioUrl);
        setProgress(100);
        setStatus('completed');
        setStatusMessage(`Realtime story audio complete. Streamed ${realtimeAudio.chunkCount} audio chunks. The same player now has the final seekable audio.`);
        return;
      } catch (streamError) {
        if (isAbortError(streamError)) throw streamError;
        if (generationRunRef.current !== runId) return;
        console.warn('[STORY AUDIO] WebSocket stream failed; falling back to job queue:', streamError);
        setProgress((current) => Math.max(current, 5));
        setStatus('queued');
        setStatusMessage('Realtime audio stream unavailable. Falling back to queued Voice Studio generation…');
      }

      const job = await createStoryAudioJob({ title: snapshot.title, text: snapshot.text, segments, voiceId: selectedVoiceId, storyDocumentId: audioMap.document.id });
      if (generationRunRef.current !== runId) return;
      streamAbortRef.current = null;
      setJobId(job.id);
      applyJobProgress(job);
      const earlySource = playableAudioSource(job);
      if (earlySource) {
        setAudioSource(earlySource);
        setStatusMessage('Streaming generated story audio…');
      }

      const completedJob = isTerminalJob(job) ? job : await pollStoryAudioJob(job.id, runId);
      if (generationRunRef.current !== runId) return;
      applyJobProgress(completedJob);
      if (completedJob.status === 'failed') throw new Error(jobErrorMessage(completedJob));
      if (completedJob.status === 'canceled') throw new Error('Voice Studio audio generation was canceled.');
      const source = playableAudioSource(completedJob);
      if (!source) throw new Error('Voice Studio did not return downloadable story audio.');
      setAudioSource(source);
      setProgress(100);
      setStatus('completed');
      setStatusMessage('Full-story audio ready to play or download.');
    } catch (error) {
      if (generationRunRef.current !== runId) return;
      streamAbortRef.current = null;
      setStatus('failed');
      setProgress(0);
      setStatusMessage(isAbortError(error) ? 'Story audio generation was stopped.' : error instanceof Error ? error.message : 'Story audio generation failed.');
    }
  }

  async function pollStoryAudioJob(id: string, runId: number): Promise<JobRecord> {
    let lastJob: JobRecord | null = null;
    for (let index = 0; index < STORY_AUDIO_MAX_POLLS; index += 1) {
      await wait(STORY_AUDIO_POLL_INTERVAL_MS);
      if (generationRunRef.current !== runId) throw new Error('Story audio generation was interrupted.');
      const job = await omnixApiClient.getJob(id);
      lastJob = job;
      applyJobProgress(job);
      const source = playableAudioSource(job);
      if (source && !audioSource) {
        setAudioSource(source);
        setStatusMessage('Streaming generated story audio…');
      }
      if (isTerminalJob(job)) return job;
    }
    throw new Error(lastJob ? 'Story audio generation timed out before completion.' : 'Story audio job never started.');
  }

  function applyJobProgress(job: JobRecord): void {
    setJobId(job.id);
    if ((job.progress?.total ?? 0) > 0) {
      setProgress(jobProgressPercent(job.progress));
    } else if (job.status === 'completed') {
      setProgress(100);
    } else if (job.status === 'running' || job.status === 'leased') {
      setProgress((current) => Math.max(current, 35));
    } else if (job.status === 'queued') {
      setProgress((current) => Math.max(current, 10));
    }
    if (job.status === 'queued' || job.status === 'running' || job.status === 'leased') {
      setStatus('running');
      setStatusMessage(`Voice Studio narration ${job.status}.`);
    }
  }

  function downloadAudio(): void {
    if (!audioSource) return;
    downloadUrl(audioSource, filename);
    setStatusMessage(`Downloaded ${filename}.`);
  }

  return (
    <section className="storyteller-audio-panel" aria-label="Story audio">
      <div className="storyteller-audio-copy">
        <p className="eyebrow">Story audio</p>
        <h3>Generate full-story narration</h3>
        <p>Use the structured story document and Voice Cast assignments to narrate the manuscript. Dialogue blocks use character voices when assigned, while narration and missing voices fall back to the narrator voice.</p>
      </div>
      <div className="storyteller-audio-controls">
        <label>
          Default narrator voice
          <select aria-label="Story audio cloned voice" disabled={isGenerating} value={selectedVoiceId} onChange={(event) => handleVoiceChange(event.currentTarget.value)}>
            <option value="">Default Voice Studio voice</option>
            {voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
          </select>
        </label>
        <div className="storyteller-audio-actions">
          <button disabled={!canGenerate} type="button" onClick={() => void generateStoryAudio()}>{isGenerating ? 'Generating audio…' : 'Generate story audio'}</button>
          <button disabled={!isGenerating} type="button" onClick={stopStoryAudio}>Stop stream</button>
          <button disabled={!audioSource} type="button" onClick={downloadAudio}>Download audio</button>
        </div>
        <div className="storyteller-audio-progress" role="status" aria-live="polite">
          <span>{statusMessage}</span>
          <progress max="100" value={Math.max(0, Math.min(100, progress))}>{progress}%</progress>
        </div>
        <audio ref={audioRef} controls preload="metadata" />
        <details className="storyteller-audio-debug">
          <summary>Audio mapping JSON</summary>
          <div className="storyteller-audio-actions">
            <button disabled={!debugAudioJson} type="button" onClick={printAudioDebugJson}>Print JSON</button>
            <button disabled={!debugAudioJson} type="button" onClick={() => void copyAudioDebugJson()}>Copy JSON</button>
          </div>
          <textarea aria-label="Story audio mapping JSON" readOnly rows={12} spellCheck={false} value={debugAudioJson || 'Generate story audio to inspect the exact websocket payload.'} />
        </details>
        <small>{jobId ? `Voice job: ${jobId}` : selectedVoiceLabel ? `Narrator voice: ${selectedVoiceLabel}` : 'Narrator voice: default'}</small>
      </div>
    </section>
  );
}

export function splitStoryAudioSegments(text: string): StoryAudioSegment[] {
  const normalized = normalizeStoryAudioText(text);
  if (!normalized) return [];
  const lines = normalized.split('\n');
  const segments: Array<{ title?: string; lines: string[] }> = [];
  let current: { title?: string; lines: string[] } = { title: 'Story', lines: [] };
  for (const line of lines) {
    const cleaned = line.replace(/^#{1,4}\s*/, '').trim();
    if (/^chapter\s+\d+\s*[:\-–—]?/i.test(cleaned)) {
      if (current.lines.join('\n').trim()) segments.push(current);
      current = { title: cleaned, lines: [cleaned] };
      continue;
    }
    current.lines.push(line);
  }
  if (current.lines.join('\n').trim()) segments.push(current);
  return segments.map((segment, index) => ({ index, speaker: 'Narrator', text: segment.lines.join('\n').trim(), title: segment.title, voice_id: null, character_id: 'narrator', block_id: `legacy-${index}`, chapter_id: `legacy-${index}` }));
}

export function voiceOptionsFromAssets(assets: AssetListResponse['assets']): StoryAudioVoiceOption[] {
  return assets
    .filter((asset) => asset.type === 'voice_profile')
    .map((asset) => ({ id: voiceAssetId(asset), label: voiceAssetLabel(asset) }))
    .filter((voice, index, allVoices) => Boolean(voice.id) && allVoices.findIndex((candidate) => candidate.id === voice.id) === index)
    .sort((left, right) => left.label.localeCompare(right.label));
}

async function createStoryAudioJob({ title, text, segments, voiceId, storyDocumentId }: { title: string; text: string; segments: StoryAudioScriptSegment[]; voiceId: string; storyDocumentId: string }): Promise<JobRecord> {
  return omnixApiClient.createJob({
    module: 'voice',
    type: segments.length > 1 ? 'tts.multi_speaker_synthesize' : 'tts.synthesize',
    resource_class: 'gpu:tts',
    priority: 1,
    input_payload: {
      text,
      provider_id: null,
      speaker: 'Narrator',
      voice_id: voiceId || null,
      script_mode: segments.length > 1 ? 'story_structured_audio' : 'single_speaker',
      story_title: title || null,
      story_document_id: storyDocumentId,
      source_module: 'storyteller',
      source_mapping: 'story_blocks_to_voice_cast',
      script_speakers: speakerRowsFromSegments(segments),
      script_segments: segments,
      character_voice_assignments: assignmentRowsFromSegments(segments),
      save_output: true,
    },
    stages: [
      { id: 'prepare-story-audio', label: 'Prepare structured story narration', resource_class: 'cpu', status: 'queued' },
      ...segments.map((segment) => ({ id: `narrate-story-${String(segment.index + 1).padStart(3, '0')}`, label: `Narrate ${segment.speaker} segment ${segment.index + 1}`, resource_class: 'gpu:tts' as const, status: 'queued' as const })),
      { id: 'stitch-story-audio', label: 'Stitch full story audio', resource_class: 'cpu', status: 'queued' },
      { id: 'store-story-audio', label: 'Save downloadable story audio', resource_class: 'cpu', status: 'queued' },
    ],
  }, { timeoutMs: 120_000, timeoutMessage: 'Story audio generation timed out after 120s.' });
}

function logStoryAudioDebugPayload(json: string): void {
  try {
    console.info('[STORY AUDIO DEBUG PAYLOAD]', JSON.parse(json));
  } catch {
    console.info('[STORY AUDIO DEBUG PAYLOAD]', json);
  }
}

/** Copies the debug payload; without a clipboard, logs it to the console and returns false. */
async function copyStoryAudioDebugPayload(json: string): Promise<boolean> {
  if (!navigator.clipboard) {
    logStoryAudioDebugPayload(json);
    return false;
  }
  await navigator.clipboard.writeText(json);
  return true;
}

function streamStoryAudioViaWebSocket(payload: StoryAudioWebSocketPayload, callbacks: StoryAudioRealtimeCallbacks): Promise<StoryAudioRealtimeResult> {
  return new Promise((resolve, reject) => {
    const WebSocketCtor = window.WebSocket;
    const audioElement = callbacks.audioElement;
    if (typeof window === 'undefined' || !WebSocketCtor) {
      reject(new Error('Realtime story audio requires browser WebSocket support.'));
      return;
    }
    if (!audioElement) {
      reject(new Error('Realtime story audio requires the Story Audio player.'));
      return;
    }

    const streamUrl = defaultStoryAudioWebSocketUrl(window.location);
    callbacks.onStatusMessage(`Connecting realtime narration stream at ${streamUrl}…`);
    const pcmChunks: Uint8Array[] = [];
    const socket = new WebSocketCtor(streamUrl);
    socket.binaryType = 'arraybuffer';
    let finished = false;
    let totalSegments = payload.segments.length;
    let firstPlayerSource = false;
    let sourceRefreshing = false;
    let userPaused = false;
    let liveObjectUrl = '';
    let lastRefreshMs = 0;
    let connectTimer: ReturnType<typeof window.setTimeout> | null = window.setTimeout(() => {
      fail(new Error(`Realtime story audio websocket did not connect within ${STORY_AUDIO_WS_CONNECT_TIMEOUT_MS / 1000}s.`));
    }, STORY_AUDIO_WS_CONNECT_TIMEOUT_MS);

    const onPlay = () => {
      if (!sourceRefreshing) userPaused = false;
    };
    const onPause = () => {
      if (!sourceRefreshing && !audioElement.ended) userPaused = true;
    };
    audioElement.addEventListener('play', onPlay);
    audioElement.addEventListener('pause', onPause);
    audioElement.pause();
    audioElement.srcObject = null;
    audioElement.removeAttribute('src');
    audioElement.load();

    const cleanup = () => {
      if (connectTimer !== null) {
        window.clearTimeout(connectTimer);
        connectTimer = null;
      }
      callbacks.signal.removeEventListener('abort', abort);
      audioElement.removeEventListener('play', onPlay);
      audioElement.removeEventListener('pause', onPause);
    };
    const revokeLiveUrl = () => {
      if (liveObjectUrl) {
        URL.revokeObjectURL(liveObjectUrl);
        liveObjectUrl = '';
      }
    };
    const finish = (result: StoryAudioRealtimeResult) => {
      if (finished) return;
      finished = true;
      cleanup();
      try { socket.close(); } catch { /* best-effort */ }
      resolve(result);
    };
    const fail = (error: unknown) => {
      if (finished) return;
      finished = true;
      cleanup();
      try { socket.close(); } catch { /* best-effort */ }
      audioElement.pause();
      audioElement.removeAttribute('src');
      audioElement.load();
      revokeLiveUrl();
      reject(error instanceof Error ? error : new Error('Realtime story audio failed.'));
    };
    function abort() {
      try {
        if (socket.readyState === WebSocketCtor.OPEN) socket.send(JSON.stringify({ type: 'stop' }));
      } catch { /* best-effort */ }
      fail(makeAbortError('Story audio stream was stopped.'));
    }

    const refreshPlayerSource = (final = false): string => {
      if (!pcmChunks.length) return liveObjectUrl;
      const now = Date.now();
      const realDuration = pcmDurationSeconds(pcmChunks, STORY_AUDIO_STREAM_SAMPLE_RATE);
      if (!final && !firstPlayerSource && realDuration < STORY_AUDIO_INITIAL_PREROLL_SECONDS) return liveObjectUrl;
      if (!final && firstPlayerSource && !audioElement.paused && audioElement.currentTime < STORY_AUDIO_STARTUP_REFRESH_HOLD_SECONDS) return liveObjectUrl;
      if (!final && firstPlayerSource && now - lastRefreshMs < STORY_AUDIO_LIVE_REFRESH_MS) return liveObjectUrl;
      lastRefreshMs = now;
      const previousUrl = liveObjectUrl;
      const currentTime = Number.isFinite(audioElement.currentTime) ? audioElement.currentTime : 0;
      const restoreTime = Math.max(0, Math.min(currentTime, Math.max(0, realDuration - 0.05)));
      const shouldPlay = !userPaused || !firstPlayerSource || audioElement.ended;
      const audioBlob = pcmChunksToWavBlob(pcmChunks, STORY_AUDIO_STREAM_SAMPLE_RATE, final ? 0 : STORY_AUDIO_LIVE_PAD_SECONDS);
      const nextUrl = URL.createObjectURL(audioBlob);
      liveObjectUrl = nextUrl;
      firstPlayerSource = true;
      sourceRefreshing = true;
      let restored = false;
      const restorePlayback = () => {
        if (restored) return;
        restored = true;
        try {
          if (restoreTime > 0) audioElement.currentTime = restoreTime;
        } catch { /* currentTime is best-effort while metadata is loading. */ }
        if (shouldPlay) void audioElement.play().catch(() => undefined);
        sourceRefreshing = false;
      };
      audioElement.addEventListener('loadedmetadata', restorePlayback, { once: true });
      audioElement.srcObject = null;
      audioElement.src = nextUrl;
      audioElement.load();
      window.setTimeout(restorePlayback, 0);
      if (previousUrl) window.setTimeout(() => URL.revokeObjectURL(previousUrl), 1_500);
      return nextUrl;
    };

    callbacks.signal.addEventListener('abort', abort, { once: true });
    if (callbacks.signal.aborted) {
      abort();
      return;
    }

    socket.onopen = () => {
      if (connectTimer !== null) {
        window.clearTimeout(connectTimer);
        connectTimer = null;
      }
      callbacks.onStatusMessage('Realtime narration connected. Building a short preroll buffer before playback…');
      socket.send(JSON.stringify(payload));
    };

    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        const chunk = event.data.slice(0);
        pcmChunks.push(new Uint8Array(chunk));
        refreshPlayerSource(false);
        return;
      }

      try {
        // Messages of other or malformed shapes are ignored.
        const message = parseJson(storyAudioStreamControlMessageSchema, String(event.data));
        if (!message) return;
        if (message.type === 'start') {
          totalSegments = typeof message.total_segments === 'number' && message.total_segments > 0 ? message.total_segments : totalSegments;
          callbacks.onStatusMessage('Realtime narration buffering through the story audio player…');
          callbacks.onProgress(8);
          return;
        }
        if (message.type === 'segment') {
          const index = typeof message.index === 'number' ? message.index : 0;
          const current = Math.min(totalSegments, index + 1);
          callbacks.onProgress(Math.min(99, Math.max(10, Math.round((current / Math.max(1, totalSegments)) * 100))));
          callbacks.onStatusMessage(`Buffering ${message.speaker || 'Narrator'} segment ${current}/${totalSegments}; playback starts after a short preroll so the opening is not clipped.`);
          return;
        }
        if (message.type === 'done') {
          callbacks.onProgress(100);
          callbacks.onStatusMessage('Realtime narration complete. Finalizing the same player as a seekable WAV…');
          const finalUrl = refreshPlayerSource(true) || URL.createObjectURL(pcmChunksToWavBlob(pcmChunks, STORY_AUDIO_STREAM_SAMPLE_RATE));
          finish({ audioUrl: finalUrl, chunkCount: pcmChunks.length });
          return;
        }
        if (message.type === 'stopped') {
          fail(makeAbortError('Story audio stream was stopped.'));
          return;
        }
        if (message.type === 'error') fail(new Error(message.message || message.error || 'Realtime story audio stream failed.'));
      } catch (error) {
        fail(error);
      }
    };
    socket.onerror = () => fail(new Error(`Realtime story audio websocket failed at ${streamUrl}.`));
    socket.onclose = () => { if (!finished) fail(new Error('Realtime story audio websocket closed before completion.')); };
  });
}

function buildStoryAudioWebSocketPayload(segments: StoryAudioScriptSegment[], fallbackVoiceId: string, jobId: string): StoryAudioWebSocketPayload {
  const voiceMapping = buildVoiceMappingFromSegments(segments, fallbackVoiceId);
  return {
    type: 'start',
    segments: segments.map((segment) => ({
      index: segment.index,
      speaker: segment.speaker,
      text: segment.text,
      voice_id: segment.voice_id,
      character_id: segment.character_id,
      block_id: segment.block_id,
      chapter_id: segment.chapter_id,
    })),
    voice_mapping: voiceMapping,
    voice_map: voiceMapping,
    default_voices: { narrator: fallbackVoiceId || null, male: fallbackVoiceId || null, female: fallbackVoiceId || null },
    job_id: jobId,
  };
}

function buildVoiceMappingFromSegments(segments: StoryAudioScriptSegment[], fallbackVoiceId: string): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const segment of segments) {
    const voiceId = segment.voice_id || (segment.speaker === 'Narrator' ? fallbackVoiceId : '') || '';
    if (!voiceId) continue;
    mapping[segment.speaker] = voiceId;
    mapping[segment.speaker.toLowerCase().trim()] = voiceId;
  }
  return mapping;
}

function defaultStoryAudioWebSocketUrl(locationLike: Pick<Location, 'protocol' | 'host' | 'hostname' | 'port'>): string {
  const configuredUrl = readConfiguredStoryAudioWebSocketUrl();
  if (configuredUrl) return configuredUrl;
  const protocol = locationLike.protocol === 'https:' ? 'wss:' : 'ws:';
  const hostname = locationLike.hostname || locationLike.host.split(':')[0] || 'localhost';
  const isLocalDevHost = ['localhost', '127.0.0.1', '0.0.0.0'].includes(hostname);
  const devUiPort = Boolean(locationLike.port && !['80', '443', STORY_AUDIO_DEV_API_PORT].includes(locationLike.port));
  const port = isLocalDevHost && devUiPort ? STORY_AUDIO_DEV_API_PORT : locationLike.port;
  return `${protocol}//${hostname}${port ? `:${port}` : ''}/ws/audiobook`;
}

function readConfiguredStoryAudioWebSocketUrl(): string {
  try {
    return typeof window !== 'undefined' ? window.localStorage.getItem(STORY_AUDIO_WS_URL_KEY)?.trim() || '' : '';
  } catch {
    return '';
  }
}

function pcmDurationSeconds(chunks: Uint8Array[], sampleRate: number): number {
  const byteLength = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  return byteLength / 2 / sampleRate;
}

function pcmChunksToWavBlob(chunks: Uint8Array[], sampleRate: number, padSeconds = 0): Blob {
  const dataSize = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  const padBytes = Math.max(0, Math.round(padSeconds * sampleRate) * 2);
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const channels = 1;
  const byteRate = sampleRate * channels * 2;
  const blockAlign = channels * 2;
  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataSize + padBytes, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, 'data');
  view.setUint32(40, dataSize + padBytes, true);
  const parts: BlobPart[] = [header];
  for (const chunk of chunks) {
    const copy = new Uint8Array(chunk.byteLength);
    copy.set(chunk);
    parts.push(copy.buffer);
  }
  if (padBytes > 0) parts.push(new ArrayBuffer(padBytes));
  return new Blob(parts, { type: 'audio/wav' });
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
}
function makeAbortError(message: string): Error { const error = new Error(message); error.name = 'AbortError'; return error; }
function isAbortError(error: unknown): boolean { return error instanceof Error && error.name === 'AbortError'; }

function playableAudioSource(job: JobRecord): string {
  for (const output of jobOutputRefs(job)) {
    if (isFallbackOutputRef(output)) continue;
    const dataUrl = typeof output.data_url === 'string' ? output.data_url : '';
    if (dataUrl.startsWith('data:audio/')) return dataUrl;
    const audioUrl = typeof output.audio_url === 'string' ? output.audio_url : '';
    if (audioUrl) return audioUrl;
    const url = typeof output.url === 'string' ? output.url : '';
    if (url && /\.(wav|mp3|ogg|webm)(\?|$)/i.test(url)) return url;
  }
  return '';
}

function isTerminalJob(job: JobRecord): boolean { return job.status === 'completed' || job.status === 'failed' || job.status === 'canceled'; }
function jobErrorMessage(job: JobRecord): string { const error = job.error as { message?: unknown } | null | undefined; return typeof error?.message === 'string' ? error.message : 'Voice Studio audio generation failed.'; }
// A voice is named by its asset id, which the TTS provider resolves (WP-4.10).
function voiceAssetId(asset: AssetListResponse['assets'][number]): string { return asset.id; }
function voiceAssetLabel(asset: AssetListResponse['assets'][number]): string { return stringValue(asset.metadata?.profile_name) || stringValue(asset.metadata?.name) || stringValue(asset.metadata?.voice_name) || basename(asset.file_name) || asset.id.replace(/^voice-cloning:/, '').replace(/^asset:/, ''); }
function voiceLabelForId(voiceId: string, voices: StoryAudioVoiceOption[]): string { return voices.find((voice) => voice.id === voiceId)?.label || voiceId; }
function stringValue(value: unknown): string { return typeof value === 'string' ? value.trim() : ''; }
function basename(path: string | undefined): string { return path?.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || ''; }
function slugify(value: string): string { return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'story'; }
function readSelectedVoiceId(): string { try { return typeof window !== 'undefined' ? window.localStorage.getItem(STORY_AUDIO_SELECTED_VOICE_KEY) ?? '' : ''; } catch { return ''; } }
function persistSelectedVoiceId(value: string): void { try { if (!value) window.localStorage.removeItem(STORY_AUDIO_SELECTED_VOICE_KEY); else window.localStorage.setItem(STORY_AUDIO_SELECTED_VOICE_KEY, value); } catch { /* Voice selection persistence is best-effort. */ } }
function wait(ms: number): Promise<void> { return new Promise((resolve) => window.setTimeout(resolve, ms)); }
