/* eslint-disable @typescript-eslint/ban-ts-comment -- baseline WP-9.x */
/* eslint-disable @typescript-eslint/no-explicit-any -- baseline WP-9.x */
/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
// @ts-nocheck
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { omnixApiClient, type AssetListResponse, type JobRecord } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { OmnixStatusPill, WorkspacePanel } from '../../design/primitives';
import { mockPodcastSpeakerProfiles } from '../conversation-production';
import { FeatureSubmitFeedback, FeatureValidationMessage } from '../../shared/FeatureSubmitFeedback';
import { createPodcastScriptSession } from './podcastScriptSession';
import { buildConversationalPodcastSegments } from './scriptBuilder';
import type { PodcastFormat } from './types';
import './PodcastWorkspace.css';
import { ApiError } from '../../api/errors';
import { fetchBytes } from '../../api/transport';
import { jobOutputRefs } from '../../api/schemas/streams';
import { downloadUrl } from '../../shared/download';
import { GatewayErrorNotice } from '../../shared/GatewayErrorNotice';

type VoiceAsset = AssetListResponse['assets'][number];
type SpeakerDraft = ReturnType<typeof toSpeakerDraft>;
type Segment = { index: number; speaker: string; text: string };
type Output = {
  dataUrl: string;
  duration: number;
  jobId: string;
  key: string;
  title: string;
  live?: boolean;
};

type WavChunk = {
  data: Uint8Array;
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
};

const defaultTitle = 'The Future of AI in Everyday Life';
const defaultBrief = 'Explore how artificial intelligence is shaping our daily lives, transforming work and productivity, inspiring creativity, influencing relationships, and augmenting decision-making. We will discuss opportunities, risks, and what comes next.';
const durations = ['2 min', '5 min', '10 min', '15 min', '20 min', '30 min', '45 min', '60 min'];
const formats: Array<{ id: PodcastFormat; label: string; description: string }> = [
  { id: 'debate', label: 'Debate', description: 'Two or more opposing sides' },
  { id: 'interview', label: 'Interview', description: 'Host interviews guests' },
  { id: 'speech', label: 'Speech', description: 'Solo host presentation' },
];
const stitchedKey = 'live:stitched-preview';
const outputSettings = { speed: 1, pitch: 0, stability: 0.72, similarity: 0.78 };
const audioEffects = ['Compression', 'De-esser'];

function toSpeakerDraft(profile: (typeof mockPodcastSpeakerProfiles)[number]) {
  return {
    id: profile.id,
    name: profile.name,
    role: profile.role,
    avatar: profile.avatar,
    identity: profile.identity,
    beliefs: profile.beliefs.join(', '),
    personality: profile.personality.join(', '),
    speakingStyle: profile.speakingStyle.join(', '),
    goal: profile.defaultGoal,
    instructions: '',
    voice: '',
  };
}

const minutes = (value: string) => Math.max(1, Number.parseInt(value, 10) || 1);
const clock = (seconds: number) => `${String(Math.floor((seconds || 0) / 60)).padStart(2, '0')}:${String(Math.floor(seconds || 0) % 60).padStart(2, '0')}`;
const isTerminal = (status: unknown) => ['completed', 'complete', 'succeeded', 'success', 'done', 'failed', 'error', 'cancelled', 'canceled'].includes(String(status ?? '').toLowerCase());
// A voice is named by its asset id, which the TTS provider resolves (WP-4.10).
const voiceName = (asset: VoiceAsset) => String((asset as any).metadata?.profile_name || (asset as any).metadata?.name || String((asset as any).file_name || '').replace(/\.[^.]+$/, '') || 'Voice');
const voicesFrom = (assets: VoiceAsset[]) => assets.filter((asset) => asset.type === 'voice_profile').map((asset) => ({ id: String(asset.id || ''), label: voiceName(asset) })).filter((voice) => voice.id);
const jobTitle = (job?: JobRecord) => String((job?.input_payload as any)?.title || job?.type || 'Podcast audio');
const rowsFrom = (segments: Segment[], total: number) => segments.map((segment, index) => ({ timestamp: clock(index * Math.max(8, total / Math.max(1, segments.length))), speaker: segment.speaker, text: segment.text }));

function stages() {
  return [
    { id: 'producer_plan', label: 'Producer Plan', resource_class: 'cpu', status: 'queued' },
    { id: 'performance_script', label: 'Performance Script', resource_class: 'cpu', status: 'queued' },
    { id: 'speaking_turns', label: 'Speaking Turns', resource_class: 'gpu:tts', status: 'queued' },
    { id: 'mix', label: 'Mix', resource_class: 'cpu', status: 'queued' },
    { id: 'podcast_renderer', label: 'Podcast Renderer', resource_class: 'cpu', status: 'queued' },
  ];
}

function outputsFrom(jobs: Array<JobRecord | undefined>): Output[] {
  return jobs.flatMap((job) => jobOutputRefs(job).map((ref, index) => {
    const url = typeof ref.data_url === 'string' ? ref.data_url : typeof ref.audio_url === 'string' ? ref.audio_url : '';
    if (!(url.startsWith('data:audio/') || url.startsWith('blob:') || url.startsWith('/api/'))) return null;
    return { dataUrl: url, duration: Number(ref.duration || 0), jobId: job?.id || 'job', key: `${job?.id}:${ref.asset_id || index}`, title: ref.title || jobTitle(job) };
  }).filter(Boolean));
}

async function waitJob(job: JobRecord, timeoutMs = 120000) {
  let latest = job;
  const started = Date.now();
  while (!outputsFrom([latest]).length && !isTerminal(latest.status) && Date.now() - started < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 1250));
    try { latest = await omnixApiClient.getJob(job.id); } catch {}
  }
  return latest;
}

function parseSegments(text: string): Segment[] {
  const match = text.replace(/```(?:json)?/g, '').replace(/```/g, '').match(/\{[\s\S]*\}|\[[\s\S]*\]/);
  if (match) {
    try {
      const parsed = JSON.parse(match[0]);
      const raw = Array.isArray(parsed) ? parsed : parsed.segments || [];
      const rows = raw.map((row: any, index: number) => ({ index, speaker: String(row.speaker || row.name || 'Speaker'), text: String(row.text || row.line || row.content || '') })).filter((row: Segment) => row.text);
      if (rows.length) return rows;
    } catch {}
  }
  return [];
}

async function generateScript(args: { title: string; brief: string; audience: string; duration: string; speakers: SpeakerDraft[] }) {
  try {
    const session = await createPodcastScriptSession({ title: `Podcast script: ${args.title}`.slice(0, 64), system_prompt: 'Return only valid JSON.' });
    const response = await omnixApiClient.sendChatMessage(session.id, {
      content: `Write a speaker-tagged podcast as JSON with segments. Topic: ${args.title}. Brief: ${args.brief}. Speakers: ${args.speakers.map((speaker) => speaker.name).join(', ')}.`,
    });
    const parsed = parseSegments(String(response?.content || response?.text || response?.message?.content || ''));
    if (parsed.length) return parsed;
  } catch {}
  return buildConversationalPodcastSegments(args.title, args.brief, args.audience, args.speakers, args.duration).map((segment, index) => ({ index, speaker: segment.speaker, text: segment.text }));
}

function assignments(segments: Segment[], speakers: SpeakerDraft[], voices: Array<{ id: string; label: string }>) {
  return Object.fromEntries([...new Set(segments.map((segment) => segment.speaker))].map((name, index) => {
    const speaker = speakers.find((entry) => entry.name === name || entry.role === name) || speakers[index % Math.max(1, speakers.length)];
    return [name, { voiceId: speaker?.voice || voices[index % Math.max(1, voices.length)]?.id || null, style: speaker?.speakingStyle || speaker?.role || 'Neutral' }];
  }));
}

const defaultVoices = (voices: any[]) => ({ narrator: voices[0]?.id || null, female: voices[1]?.id || voices[0]?.id || null, male: voices[2]?.id || voices[0]?.id || null });

function previewPayload(args: any) {
  const assignment = args.assignments[args.segment.speaker] || {};
  const voiceId = assignment.voiceId || args.voices[0]?.id || null;
  return {
    title: `${args.title} - ${args.segment.speaker} ${args.segment.index + 1}`,
    text: args.segment.text,
    provider_id: null,
    speaker: args.segment.speaker,
    voice_id: voiceId,
    language: args.language,
    script_mode: 'single_speaker',
    script_speakers: [{ name: args.segment.speaker, count: 1 }],
    script_segments: [args.segment],
    character_voice_assignments: [{ speaker: args.segment.speaker, voice_id: voiceId, style: assignment.style || 'Neutral', line_count: 1 }],
    voice_mapping: { [args.segment.speaker]: voiceId },
    default_voices: defaultVoices(args.voices),
    output_settings: outputSettings,
    audio_effects: audioEffects,
    save_output: true,
    renderer: 'podcast-live-preview',
  };
}

function finalPayload(args: any) {
  const counts = args.segments.reduce((acc: any, segment: Segment) => ({ ...acc, [segment.speaker]: (acc[segment.speaker] || 0) + 1 }), {});
  const names = Object.keys(counts);
  const map = Object.fromEntries(names.map((name) => [name, args.assignments[name]?.voiceId || null]));
  return {
    title: args.title,
    brief: args.brief,
    format: args.format,
    audience: args.audience,
    duration_minutes: minutes(args.duration),
    target_duration_seconds: minutes(args.duration) * 60,
    target_word_count: minutes(args.duration) * 150,
    tone: args.tone,
    language: args.language,
    generation_style: args.generationStyle,
    review_policy: { mode: args.generationStyle },
    renderer: 'podcast',
    text: args.segments.map((segment: Segment) => `${segment.speaker}: ${segment.text}`).join('\n'),
    provider_id: null,
    speaker: names[0] || args.speakers[0]?.name || 'Host',
    voice_id: map[names[0]] || args.voices[0]?.id || null,
    script_mode: args.segments.length > 1 ? 'multi_speaker' : 'single_speaker',
    script_speakers: names.map((name) => ({ name, count: counts[name] })),
    script_segments: args.segments,
    character_voice_assignments: names.map((name) => ({ speaker: name, voice_id: map[name] || null, style: args.assignments[name]?.style || 'Neutral', line_count: counts[name] })),
    voice_mapping: map,
    default_voices: defaultVoices(args.voices),
    output_settings: outputSettings,
    audio_effects: audioEffects,
    save_output: true,
    speakers: args.speakers.map((speaker: SpeakerDraft) => ({ id: speaker.id, name: speaker.name, role: speaker.role, speakerInstructions: speaker.instructions, voiceMapping: { speakerId: speaker.id, voiceId: speaker.voice } })),
    constraints: { maxSpeakerTurnSeconds: 45 },
  };
}

function readAscii(view: DataView, offset: number, length: number) {
  let value = '';
  for (let index = 0; index < length && offset + index < view.byteLength; index += 1) value += String.fromCharCode(view.getUint8(offset + index));
  return value;
}

function writeAscii(bytes: Uint8Array, offset: number, value: string) {
  for (let index = 0; index < value.length; index += 1) bytes[offset + index] = value.charCodeAt(index);
}

function parseWavChunk(bytes: Uint8Array): WavChunk {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.byteLength >= 44 && readAscii(view, 0, 4) === 'RIFF' && readAscii(view, 8, 4) === 'WAVE') {
    let cursor = 12;
    let sampleRate = 24000;
    let channels = 1;
    let bitsPerSample = 16;
    let data: Uint8Array | null = null;

    while (cursor + 8 <= view.byteLength) {
      const id = readAscii(view, cursor, 4);
      const size = view.getUint32(cursor + 4, true);
      const start = cursor + 8;
      const end = Math.min(start + size, bytes.byteLength);

      if (id === 'fmt ' && size >= 16 && start + 16 <= view.byteLength) {
        channels = view.getUint16(start + 2, true) || 1;
        sampleRate = view.getUint32(start + 4, true) || 24000;
        bitsPerSample = view.getUint16(start + 14, true) || 16;
      }
      if (id === 'data' && end > start) data = bytes.slice(start, end);
      cursor = end + (size % 2);
    }

    if (data) return { data, sampleRate, channels, bitsPerSample };
  }

  return { data: bytes, sampleRate: 24000, channels: 1, bitsPerSample: 16 };
}

function createWavBytes(chunks: WavChunk[]) {
  const first = chunks[0] ?? { sampleRate: 24000, channels: 1, bitsPerSample: 16, data: new Uint8Array() };
  const sampleRate = first.sampleRate || 24000;
  const channels = first.channels || 1;
  const bitsPerSample = first.bitsPerSample || 16;
  const blockAlign = Math.max(1, Math.floor((channels * bitsPerSample) / 8));
  const byteRate = sampleRate * blockAlign;
  const dataSize = chunks.reduce((sum, chunk) => sum + chunk.data.byteLength, 0);
  const bytes = new Uint8Array(44 + dataSize);
  const view = new DataView(bytes.buffer);

  writeAscii(bytes, 0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(bytes, 8, 'WAVE');
  writeAscii(bytes, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeAscii(bytes, 36, 'data');
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (const chunk of chunks) {
    bytes.set(chunk.data, offset);
    offset += chunk.data.byteLength;
  }

  const computedDuration = dataSize / Math.max(1, byteRate);
  return { bytes, duration: computedDuration };
}

async function loadAudioBytes(url: string) {
  const response = await fetchBytes(url).catch((error: unknown) => {
    throw error instanceof ApiError ? new Error(`Audio fetch failed: ${error.status}`) : error;
  });
  return new Uint8Array(await response.arrayBuffer());
}

async function stitchOutputs(outputs: Output[]) {
  if (!outputs.length) return null;
  const chunks: WavChunk[] = [];
  for (const output of outputs) chunks.push(parseWavChunk(await loadAudioBytes(output.dataUrl)));
  const wav = createWavBytes(chunks);
  const blobUrl = URL.createObjectURL(new Blob([wav.bytes], { type: 'audio/wav' }));
  const last = outputs[outputs.length - 1];
  const summedDuration = outputs.reduce((sum, output) => sum + Number(output.duration || 0), 0);
  return {
    ...last,
    dataUrl: blobUrl,
    key: `${stitchedKey}:${outputs.length}:${last.key}`,
    title: `Live preview stitched ${outputs.length} / ${outputs.length}`,
    live: true,
    duration: wav.duration || summedDuration || last.duration,
  };
}

export function PodcastWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const queryClient = useQueryClient();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const currentRef = useRef<Output | null>(null);
  const pendingRef = useRef<Output | null>(null);
  const resumeNextRef = useRef(false);
  const userPausedRef = useRef(false);
  const autoStartedRef = useRef(false);
  const restoreRef = useRef({ time: 0, play: false });
  const piecesRef = useRef<Output[]>([]);
  const stitchRunRef = useRef(0);
  const liveBlobUrlsRef = useRef<string[]>([]);

  const jobsQuery = useQuery({ queryKey: ['platform', 'jobs'], queryFn: () => omnixApiClient.listJobs(), retry: false, refetchInterval: false, refetchOnWindowFocus: false });
  const assetsQuery = useQuery({ queryKey: ['platform', 'assets'], queryFn: () => omnixApiClient.listAssets(), retry: false, refetchInterval: false, refetchOnWindowFocus: false });

  const [title, setTitle] = useState(defaultTitle);
  const [brief, setBrief] = useState(defaultBrief);
  const [audience, setAudience] = useState('Software Engineers');
  const [duration, setDuration] = useState('20 min');
  const [tone, setTone] = useState('Professional');
  const [language, setLanguage] = useState('English (US)');
  const [format, setFormat] = useState<PodcastFormat>('debate');
  const [generationStyle, setGenerationStyle] = useState('automatic');
  const [speakers, setSpeakers] = useState(() => mockPodcastSpeakerProfiles.map(toSpeakerDraft));
  const [rows, setRows] = useState<any[]>([]);
  const [directorNote, setDirectorNote] = useState('No live production is running. Configure the episode, then press Generate live podcast.');
  const [liveCommand, setLiveCommand] = useState('');
  const [playbackRate, setPlaybackRate] = useState('1.0x');
  const [selectedOutputKey, setSelectedOutputKey] = useState('');
  const [pieces, setPieces] = useState<Output[]>([]);
  const [current, setCurrent] = useState<Output | null>(null);
  const [autoplay, setAutoplay] = useState(false);
  const [message, setMessage] = useState('Ready for automatic production.');

  const voices = useMemo(() => voicesFrom(assetsQuery.data?.assets ?? []), [assetsQuery.data?.assets]);
  const podcastJobs = jobsQuery.data?.jobs?.filter((job: JobRecord) => job.module === 'podcast') ?? [];

  function install(output: Output, time = audioRef.current?.currentTime || 0, play = false) {
    restoreRef.current = { time, play };
    currentRef.current = output;
    setCurrent(output);
    setSelectedOutputKey(output.key);
  }

  async function queue(output: Output) {
    const nextPieces = [...piecesRef.current, output];
    piecesRef.current = nextPieces;
    setPieces(nextPieces);
    setSelectedOutputKey(stitchedKey);

    const stitchRun = ++stitchRunRef.current;
    const joined = await stitchOutputs(nextPieces).catch((error) => {
      console.warn('[PODCAST] Failed to stitch live preview audio:', error);
      return null;
    });
    if (!joined || stitchRun !== stitchRunRef.current) {
      if (joined?.dataUrl?.startsWith('blob:')) URL.revokeObjectURL(joined.dataUrl);
      return;
    }

    liveBlobUrlsRef.current.push(joined.dataUrl);
    const audio = audioRef.current;
    const restoreTime = currentRef.current?.duration || audio?.currentTime || 0;
    if (currentRef.current?.live && audio && !audio.paused && !audio.ended) {
      pendingRef.current = joined;
      return;
    }

    const play = Boolean(currentRef.current?.live && resumeNextRef.current && !userPausedRef.current);
    resumeNextRef.current = false;
    pendingRef.current = null;
    install(joined, restoreTime, play);
  }

  function resetPreview() {
    try { if (audioRef.current?.getAttribute('src')) audioRef.current.pause(); } catch {}
    stitchRunRef.current += 1;
    pendingRef.current = null;
    resumeNextRef.current = false;
    userPausedRef.current = false;
    autoStartedRef.current = false;
    currentRef.current = null;
    piecesRef.current = [];
    for (const url of liveBlobUrlsRef.current) URL.revokeObjectURL(url);
    liveBlobUrlsRef.current = [];
    setCurrent(null);
    setPieces([]);
  }

  const createJobMutation = useMutation({
    mutationFn: async () => {
      const segments = await generateScript({ title, brief, audience, duration, speakers });
      const asg = assignments(segments, speakers, voices);
      setRows(rowsFrom(segments, minutes(duration) * 60));
      setAutoplay(true);
      setSelectedOutputKey('__streaming__');
      setDirectorNote('Script is ready. Each completed preview turn is stitched into one growing WAV so the audio duration extends instead of resetting to the latest segment.');

      for (const segment of segments) {
        const job = await omnixApiClient.createJob({
          module: 'podcast',
          type: 'tts.synthesize',
          resource_class: 'gpu:tts',
          priority: 1,
          input_payload: previewPayload({ title, language, segment, voices, assignments: asg }),
          stages: stages().slice(0, 3),
        }, { timeoutMs: 120000, timeoutMessage: 'Podcast live preview turn is still running.' });
        const output = outputsFrom([await waitJob(job)])[0];
        if (output) {
          await queue({ ...output, key: `live:${segment.index}:${output.key}`, title: `${segment.speaker}: voice mapped`, live: true });
          setMessage(`Stitched live preview ${segment.index + 1}/${segments.length}: ${segment.speaker}`);
        }
      }

      const finalJob = await omnixApiClient.createJob({
        module: 'podcast',
        type: 'tts.multi_speaker_synthesize',
        resource_class: 'gpu:tts',
        priority: 0,
        input_payload: finalPayload({ title, brief, format, audience, duration, tone, language, generationStyle, speakers, voices, assignments: asg, segments }),
        stages: stages(),
      }, { timeoutMs: 180000, timeoutMessage: 'Final podcast render is still running.' });
      return waitJob(finalJob, 180000);
    },
    onMutate: () => {
      resetPreview();
      setRows([]);
      setAutoplay(false);
      setSelectedOutputKey('__script__');
      setDirectorNote('Generating a real speaker-tagged script with the configured chat provider.');
      setMessage('Requesting podcast script from LLM...');
    },
    onSuccess: async (job) => {
      const output = outputsFrom([job])[0];
      setDirectorNote(output ? 'Final podcast audio is ready. The stitched live preview stays active until you select the final render.' : 'Podcast render completed but no playable audio output was attached.');
      setMessage(output ? `Podcast audio ready: ${job.id}` : `Podcast production completed without playable audio: ${job.id}`);
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }), queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] })]);
    },
    onError: (error) => {
      setDirectorNote(error instanceof Error ? error.message : 'Podcast request failed.');
      setMessage(error instanceof Error ? error.message : 'Podcast request failed.');
    },
  });

  const connectedJob = createJobMutation.data ?? podcastJobs[0];
  const liveStatus = createJobMutation.isPending ? (selectedOutputKey === '__script__' ? 'SCRIPTING' : 'STREAMING') : connectedJob ? String(connectedJob.status).toUpperCase() : 'IDLE';
  const liveActive = createJobMutation.isPending || Boolean(podcastJobs.find((job) => !isTerminal(job.status)));
  const visibleStages = createJobMutation.isPending ? stages().map((stage, index) => ({ ...stage, state: index < 2 ? 'done' : index === 2 ? 'active' : 'pending' })) : stages().map((stage) => ({ ...stage, state: 'pending' }));
  const recentJobs = podcastJobs.slice(0, 6).map((job) => ({ id: job.id, name: jobTitle(job), status: job.status, duration }));

  useEffect(() => { currentRef.current = current; }, [current]);
  useEffect(() => () => { for (const url of liveBlobUrlsRef.current) URL.revokeObjectURL(url); }, []);
  useEffect(() => { if (voices.length) setSpeakers((currentSpeakers) => currentSpeakers.map((speaker, index) => speaker.voice && voices.some((voice) => voice.id === speaker.voice) ? speaker : { ...speaker, voice: voices[index % voices.length].id })); }, [voices.map((voice) => voice.id).join('|')]);
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !current) return;
    audio.playbackRate = Number.parseFloat(playbackRate) || 1;
    if (!autoplay || !current.live || autoStartedRef.current) return;
    autoStartedRef.current = true;
    const timer = window.setTimeout(() => audio.play()?.catch?.(() => setMessage('Audio is ready. Press play if the browser blocked autoplay.')), 50);
    return () => window.clearTimeout(timer);
  }, [current?.key, current?.dataUrl, autoplay, playbackRate]);

  function onPause() {
    const audio = audioRef.current;
    if (naturalEnd(audio) && createJobMutation.isPending) return;
    userPausedRef.current = true;
    resumeNextRef.current = false;
    if (pendingRef.current && (!audio || audio.paused || audio.ended)) {
      const next = pendingRef.current;
      pendingRef.current = null;
      install(next, audio?.currentTime || 0, false);
    }
  }
  function onLoaded(event: any) {
    const audio = event.currentTarget;
    const restore = restoreRef.current;
    audio.playbackRate = Number.parseFloat(playbackRate) || 1;
    if (restore.time) audio.currentTime = Math.min(restore.time + (restore.play ? 0.02 : 0), Math.max(0, audio.duration - 0.05));
    if (restore.play) audio.play()?.catch?.(() => setMessage('Audio is ready. Press play if the browser blocked autoplay.'));
    restoreRef.current = { time: 0, play: false };
  }
  function onEnded() {
    const audio = audioRef.current;
    if (pendingRef.current) {
      const next = pendingRef.current;
      pendingRef.current = null;
      install(next, currentRef.current?.duration || audio?.currentTime || 0, !userPausedRef.current);
      return;
    }
    if (currentRef.current?.live && createJobMutation.isPending && !userPausedRef.current) {
      resumeNextRef.current = true;
      setMessage('Waiting for the next stitched preview chunk...');
      return;
    }
    const finalOutput = outputsFrom([createJobMutation.data, ...podcastJobs]).find((output) => !output.live);
    if (finalOutput) install(finalOutput);
  }

  function startGeneration() { if (brief.trim()) createJobMutation.mutate(); }
  function updateSpeaker(id: string, field: string, value: string) { setSpeakers((currentSpeakers) => currentSpeakers.map((speaker) => speaker.id === id ? { ...speaker, [field]: value } : speaker)); }
  function addParticipant() {
    const index = speakers.length + 1;
    setSpeakers((currentSpeakers) => [...currentSpeakers, { id: `guest_${index}`, name: `Guest ${index}`, role: 'Guest Analyst', avatar: `G${index}`, identity: 'Guest Analyst', beliefs: '', personality: '', speakingStyle: '', goal: '', instructions: '', voice: voices[0]?.id ?? '' }]);
  }
  function resetPodcast() { resetPreview(); setTitle(''); setBrief(''); setDuration('5 min'); setRows([]); setSelectedOutputKey('__new__'); setMessage('New podcast ready.'); }
  function submitLiveCommand() {
    const text = liveCommand.trim();
    if (text) {
      setDirectorNote(`Director note: ${text}`);
      setRows((currentRows) => [...currentRows, { timestamp: clock(currentRows.length * 15), speaker: 'Director', text }]);
      setLiveCommand('');
    }
  }
  function selectRecentJob(id: string) { const output = outputsFrom([podcastJobs.find((job) => job.id === id)])[0]; if (output) install(output); }
  function downloadCurrentOutput(label = 'Podcast audio') { if (!current) return; downloadUrl(current.dataUrl, `${title || 'podcast-output'}.wav`); setMessage(`${label}: download started.`); }
  async function copyEpisodeLink() { try { await navigator.clipboard?.writeText(`${location.href.split('#')[0]}#${connectedJob?.id ?? 'podcast'}`); setMessage('Podcast link copied.'); } catch {} }

  return (
    <WorkspacePanel labelledBy="module-title" className="podcast-workspace-panel">
      <div className="podcast-studio-shell">
        <header className="podcast-studio-header">
          <div>
            <p className="eyebrow">Conversation engine</p>
            <h2 id="module-title">{module.label}</h2>
            <p>Create a real LLM-generated podcast script, stitch live preview turns into one growing stream, then keep one final render for playback and download.</p>
          </div>
          <code>/podcast-renderer</code>
        </header>
        <GatewayErrorNotice label="Podcast jobs and voices" errors={[jobsQuery.error, assetsQuery.error]} />

        <div className="podcast-studio-grid">
          <section className="podcast-studio-stack">
            <article className="podcast-card episode-setup-card">
              <div className="card-heading-row"><h3>1. Episode setup</h3><button className="ghost-button compact" type="button" onClick={resetPodcast}>New podcast</button></div>
              <div className="episode-setup-grid">
                <div className="podcast-field-stack">
                  <label>Topic / Episode title<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
                  <label>Episode brief<textarea rows={5} value={brief} onChange={(event) => setBrief(event.target.value)} /><small>{brief.length}/2000</small></label>
                  <label>Audience<select value={audience} onChange={(event) => setAudience(event.target.value)}><option>Software Engineers</option><option>General Public</option><option>Executives</option><option>Students</option><option>Experts</option></select></label>
                </div>
                <div className="podcast-config-stack">
                  <span className="podcast-label">Podcast format</span>
                  <div className="format-card-grid">{formats.map((option) => <button key={option.id} type="button" className={option.id === format ? 'selected' : undefined} onClick={() => setFormat(option.id)}><strong>{option.label}</strong><small>{option.description}</small></button>)}</div>
                  <div className="podcast-select-grid">
                    <label>Duration<select value={duration} onChange={(event) => setDuration(event.target.value)}>{durations.map((option) => <option key={option}>{option}</option>)}</select></label>
                    <label>Tone<select value={tone} onChange={(event) => setTone(event.target.value)}><option>Professional</option><option>Conversational</option><option>Humorous</option></select></label>
                    <label>Language<select value={language} onChange={(event) => setLanguage(event.target.value)}><option>English (US)</option><option>English (UK)</option></select></label>
                  </div>
                  <label>Generation Style<select value={generationStyle} onChange={(event) => setGenerationStyle(event.target.value)}><option value="automatic">Automatic</option><option value="guided">Guided</option></select></label>
                </div>
              </div>
            </article>

            <article className="podcast-card">
              <div className="card-heading-row"><h3>2. Participants and voice casting</h3><small>{voices.length ? `Loaded ${voices.length} Voice Library voice${voices.length === 1 ? '' : 's'}` : 'No Voice Library voices found'}</small></div>
              <div className="speaker-table editable-speaker-table">
                <div className="speaker-row speaker-header"><span>Speaker</span><span>Identity</span><span>Voice</span><span>Beliefs</span><span>Personality</span><span>Speaking style</span><span>Goal this episode</span><span>Instructions</span></div>
                {speakers.map((speaker, index) => (
                  <div className="speaker-row editable-speaker-row" key={speaker.id}>
                    <span className="speaker-cell-main"><b className={`speaker-avatar ${speaker.id}`}>{speaker.avatar}</b><span><input aria-label={`Speaker ${index + 1} name`} value={speaker.name} onChange={(event) => updateSpeaker(speaker.id, 'name', event.target.value)} /><input aria-label={`Speaker ${index + 1} role`} value={speaker.role} onChange={(event) => updateSpeaker(speaker.id, 'role', event.target.value)} /></span></span>
                    <span><input aria-label={`Speaker ${index + 1} identity`} value={speaker.identity} onChange={(event) => updateSpeaker(speaker.id, 'identity', event.target.value)} /></span>
                    <span><select aria-label={`${speaker.name} voice`} value={speaker.voice} onChange={(event) => updateSpeaker(speaker.id, 'voice', event.target.value)}>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}{!voices.length ? <option value="">No cloned voices</option> : null}</select></span>
                    <span><textarea rows={2} aria-label={`Speaker ${index + 1} beliefs`} value={speaker.beliefs} onChange={(event) => updateSpeaker(speaker.id, 'beliefs', event.target.value)} /></span>
                    <span><textarea rows={2} aria-label={`Speaker ${index + 1} personality`} value={speaker.personality} onChange={(event) => updateSpeaker(speaker.id, 'personality', event.target.value)} /></span>
                    <span><textarea rows={2} aria-label={`Speaker ${index + 1} speaking style`} value={speaker.speakingStyle} onChange={(event) => updateSpeaker(speaker.id, 'speakingStyle', event.target.value)} /></span>
                    <span><textarea rows={2} aria-label={`Speaker ${index + 1} goal this episode`} value={speaker.goal} onChange={(event) => updateSpeaker(speaker.id, 'goal', event.target.value)} /></span>
                    <span><textarea rows={2} aria-label={`Speaker ${index + 1} instructions`} value={speaker.instructions} onChange={(event) => updateSpeaker(speaker.id, 'instructions', event.target.value)} /></span>
                  </div>
                ))}
              </div>
              <button className="ghost-button" type="button" onClick={addParticipant}>+ Add participant</button>
            </article>

            <form onSubmit={(event) => { event.preventDefault(); startGeneration(); }}>
              <button className="podcast-generate-button" type="submit" disabled={createJobMutation.isPending}>Generate live podcast</button>
            </form>
            <FeatureValidationMessage show={brief.trim().length === 0 && !createJobMutation.isIdle} message="Enter an episode brief before generating a podcast." />
            <FeatureSubmitFeedback error={createJobMutation.error} errorPrefix="Podcast request" isError={createJobMutation.isError} isPending={createJobMutation.isPending} jobId={createJobMutation.data?.id} pendingMessage="Generating script and stitching preview audio..." successPrefix="Podcast production queued" />
          </section>

          <section className="podcast-live-column">
            <article className={`podcast-card live-production-card ${liveActive ? 'streaming' : 'idle'}`}>
              <div className="card-heading-row"><h3>Live production</h3><span className="auto-badge">{liveStatus}</span></div>
              <div className="stage-rail">{visibleStages.map((stage, index) => <span key={stage.id} className={stage.state}>{stage.state === 'done' ? 'OK' : index + 1}<small>{stage.label}</small></span>)}</div>
              <div className="director-note"><b>Director</b><span>{directorNote}</span></div>

              <section className="live-transcript-section">
                <div className="card-heading-row"><h4>Transcript</h4><small>{rows.length ? `${rows.length} line${rows.length === 1 ? '' : 's'}` : 'Waiting for script'}</small></div>
                <div className="live-transcript">
                  {rows.length ? rows.map((line, index) => <p key={`${line.timestamp}-${index}`}><time>{line.timestamp}</time><b title={line.speaker}>{String(line.speaker).slice(0, 18)}</b><span>{line.text}</span></p>) : <div className="live-empty-state"><strong>No live transcript yet</strong><span>Press Generate live podcast to request an LLM script and start live preview audio.</span></div>}
                </div>
              </section>

              <div role="group" className="podcast-audio-player" aria-label="Podcast audio player">
                <div className="audio-player-heading">
                  <span>{current ? current.title : selectedOutputKey === '__script__' ? 'Generating podcast script...' : selectedOutputKey === '__streaming__' || selectedOutputKey === stitchedKey ? 'Waiting for first stitched live audio segment...' : 'No podcast audio yet'}</span>
                  <small>{current?.live ? `LIVE STITCHED ${pieces.length} / ${rows.length || pieces.length}` : current ? 'AUDIO READY' : createJobMutation.isPending ? liveStatus : 'Generate a completed podcast to enable playback'}</small>
                </div>
                <audio key={current?.key || selectedOutputKey || 'podcast-empty-audio'} ref={audioRef} src={current?.dataUrl || undefined} controls preload="auto" onLoadedMetadata={onLoaded} onCanPlay={() => current && setMessage(`${current.live ? 'Stitched live preview' : 'Audio available'}: ${current.title}`)} onPause={onPause} onPlay={() => { userPausedRef.current = false; }} onEnded={onEnded} />
                <div className="audio-toolbar"><label>Playback speed<select value={playbackRate} onChange={(event) => setPlaybackRate(event.target.value)}><option>0.8x</option><option>1.0x</option><option>1.25x</option></select></label><small>{current ? `${clock(current.duration)} ${current.live ? 'stitched preview' : 'rendered'}` : 'No audio loaded'}</small></div>
              </div>

              <form className="live-command" onSubmit={(event) => { event.preventDefault(); submitLiveCommand(); }}><input value={liveCommand} onChange={(event) => setLiveCommand(event.target.value)} placeholder="Add a production note" /><button type="submit" disabled={!liveCommand.trim()}>Apply</button></form>
            </article>

            <article className="podcast-card podcast-output-panel live-output-panel">
              <h3>Podcast outputs</h3>
              <div className="output-layout">
                <div className="cover-art">AI<br />EVERYDAY<br />LIFE</div>
                <div className="output-copy"><h4>{title || 'Untitled episode'} <span>{connectedJob ? String(connectedJob.status).toUpperCase() : createJobMutation.isPending ? liveStatus : 'IDLE'}</span></h4><small>{formats.find((option) => option.id === format)?.label} - {speakers.length} voices - {duration}</small>{current ? <em>{current.title}</em> : null}</div>
                <div className="download-grid"><button type="button" disabled={!current} onClick={() => downloadCurrentOutput('MP3')}>MP3</button><button type="button" disabled={!current} onClick={() => downloadCurrentOutput('WAV')}>WAV</button><button type="button" onClick={() => setMessage('Transcript export requested.')}>Transcript</button><button type="button" onClick={() => setMessage('Show notes export requested.')}>Show Notes</button><button type="button" onClick={() => void copyEpisodeLink()}>Copy link</button></div>
              </div>
            </article>
          </section>

          <aside className="podcast-sidebar">
            <article className="podcast-card recent-card collapsible-card">
              <h3>Recent jobs</h3>
              <div className="recent-job-list">{recentJobs.map((job) => <p key={job.id}><span className="recent-job-title">{job.name}</span><OmnixStatusPill>{job.status}</OmnixStatusPill><small>{job.duration}</small><button type="button" onClick={() => selectRecentJob(job.id)}>Select</button></p>)}</div>
            </article>
          </aside>
        </div>
        <p className="action-toast" role="status">{message}</p>
      </div>
    </WorkspacePanel>
  );
}

/** Whether playback reached the end of the audio (not a pause near it). */
function naturalEnd(audio: HTMLAudioElement | null) { return Boolean(audio && Number.isFinite(audio.duration) && audio.duration > 0 && audio.currentTime >= audio.duration - 0.18); }
