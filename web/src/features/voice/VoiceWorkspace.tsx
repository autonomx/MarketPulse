/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
/* eslint-disable react-hooks/rules-of-hooks -- baseline WP-9.x */
import { Button, Group, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import { omnixApiClient, type AssetListResponse, type JobRecord, type ProviderFacadePayload } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { OmnixStatusPill, WorkspacePanel } from '../../design/primitives';
import { voiceStudioDefaults } from '../settings';
import { loadSettingsProfile } from '../settings';
import { FeatureSubmitFeedback, FeatureValidationMessage } from '../../shared/FeatureSubmitFeedback';
import { DEFAULT_OUTPUT_SETTINGS } from './outputDefaults';
import { firstResultAsset } from './resultList';
import { parseScriptSegments, parseScriptSpeakers, type ScriptSegmentRow, type ScriptSpeakerRow } from './scriptLines';
import './VoiceStudioWorkspace.css';
import { jobProgressPercent } from '../../api/jobProgress';
import { isFallbackOutputRef, jobOutputRefs, type JobOutputRef } from '../../api/schemas/streams';
import { downloadUrl } from '../../shared/download';
import { voiceApiClient } from './api/voiceClient';
import { ENGINES_QUERY_KEY, TtsEnginePicker } from './TtsEnginePicker';

interface VoiceFormValues {
  text: string;
  providerId: string;
  speaker: string;
  voiceId: string;
}

interface VoiceCloneFormValues {
  providerId: string;
  profileName: string;
  language: string;
  quality: string;
  notes: string;
  referenceText: string;
  generateTranscript: boolean;
}


interface PlayableVoiceOutput {
  dataUrl: string;
  duration: number;
  jobId: string;
  key: string;
  title: string;
}

type VoiceAsset = AssetListResponse['assets'][number];
type CloneSourceKind = 'upload' | 'record';
type OutputSettingName = keyof typeof DEFAULT_OUTPUT_SETTINGS;
type JobQueueFilter = 'active' | 'recent' | 'failed';

const DEFAULT_SCRIPT = 'dave: hello there\nbob: how do you do\nmarry: i am doing fine\ndave: now lets get to the topic\nmarry: agreed.';
const STYLE_OPTIONS = ['Confident, Conversational', 'Calm', 'Enthusiastic', 'Warm', 'Deep, Authoritative', 'Narrator, Clear'];
const AUDIO_EFFECTS = ['Equalizer', 'Reverb', 'Compression', 'De-esser', 'Noise Reduction'];

export function VoiceWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const queryClient = useQueryClient();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const appliedSettingsRevision = useRef('');
  const providersQuery = useQuery({ queryKey: ['platform', 'providers'], queryFn: () => omnixApiClient.listProviders() });
  // Under ['platform', 'jobs'] so job invalidations refresh it; bounded to voice jobs.
  const jobsQuery = useQuery({ queryKey: ['platform', 'jobs', 'voice-summaries'], queryFn: () => voiceApiClient.listVoiceJobSummaries() });
  const assetsQuery = useQuery({ queryKey: ['platform', 'assets'], queryFn: () => omnixApiClient.listAssets() });
  const settingsQuery = useQuery({ queryKey: ['settings', 'profile'], queryFn: () => loadSettingsProfile() });
  const enginesQuery = useQuery({ queryKey: ENGINES_QUERY_KEY, queryFn: () => voiceApiClient.listTtsEngines() });
  const selectedEngine = enginesQuery.data?.engines.find((engine) => engine.selected);
  const moduleDefaults = useMemo(() => voiceStudioDefaults(settingsQuery.data?.profile), [settingsQuery.data?.profile]);
  const centralOutputSettings = useMemo(() => ({
    stability: moduleDefaults.stability,
    similarity: moduleDefaults.similarity,
    style: moduleDefaults.style,
    speed: moduleDefaults.speed,
    pitch: moduleDefaults.pitch,
    volume: moduleDefaults.volume,
  }), [moduleDefaults.pitch, moduleDefaults.similarity, moduleDefaults.speed, moduleDefaults.stability, moduleDefaults.style, moduleDefaults.volume]);
  const [cloneSource, setCloneSource] = useState<CloneSourceKind>('upload');
  const [sampleFile, setSampleFile] = useState<File | null>(null);
  const [recordedSample, setRecordedSample] = useState<Blob | null>(null);
  const [mediaRecorder, setMediaRecorder] = useState<MediaRecorder | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState('');
  const [voiceSearch, setVoiceSearch] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [showAllVoices, setShowAllVoices] = useState(false);
  const [jobQueueFilter, setJobQueueFilter] = useState<JobQueueFilter>('active');
  const [selectedOutputKey, setSelectedOutputKey] = useState('');
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [playbackDuration, setPlaybackDuration] = useState(0);
  const [speakerVoiceAssignments, setSpeakerVoiceAssignments] = useState<Record<string, string>>({});
  const [speakerStyleAssignments, setSpeakerStyleAssignments] = useState<Record<string, string>>({});
  const [outputSettings, setOutputSettings] = useState(centralOutputSettings);
  const [enabledEffects, setEnabledEffects] = useState<string[]>(moduleDefaults.effects);
  const [tuningDirty, setTuningDirty] = useState(false);
  const [effectsDirty, setEffectsDirty] = useState(false);
  const [pendingPlaybackJobId, setPendingPlaybackJobId] = useState('');
  const [completedPlaybackJob, setCompletedPlaybackJob] = useState<JobRecord | null>(null);
  const pendingPlaybackJobQuery = useQuery({
    queryKey: ['platform', 'jobs', pendingPlaybackJobId],
    queryFn: () => omnixApiClient.getJob(pendingPlaybackJobId),
    enabled: Boolean(pendingPlaybackJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'queued' || status === 'running' || status === 'leased' ? 1000 : false;
    },
  });

  const {
    register,
    handleSubmit,
    setValue,
    reset,
    getValues,
    watch,
    formState: { errors, isDirty: voiceFormDirty },
  } = useForm<VoiceFormValues>({
    defaultValues: { text: DEFAULT_SCRIPT, providerId: moduleDefaults.providerId, speaker: '', voiceId: '' },
  });
  const {
    register: registerClone,
    handleSubmit: handleCloneSubmit,
    reset: resetClone,
    getValues: getCloneValues,
    setValue: setCloneValue,
    formState: { errors: cloneErrors, isDirty: cloneFormDirty },
  } = useForm<VoiceCloneFormValues>({
    defaultValues: { providerId: moduleDefaults.voiceCloningProviderId, profileName: '', language: moduleDefaults.cloningLanguage, quality: moduleDefaults.cloningQuality, notes: '', referenceText: '', generateTranscript: true },
  });

  useEffect(() => {
    const revision = settingsQuery.data?.profile.revision;
    if (!revision || appliedSettingsRevision.current === revision) return;
    if (!voiceFormDirty) reset({ ...getValues(), providerId: moduleDefaults.providerId });
    if (!cloneFormDirty) resetClone({ providerId: moduleDefaults.voiceCloningProviderId, profileName: '', language: moduleDefaults.cloningLanguage, quality: moduleDefaults.cloningQuality, notes: '', referenceText: '', generateTranscript: true });
    if (!tuningDirty) setOutputSettings(centralOutputSettings);
    if (!effectsDirty) setEnabledEffects([...moduleDefaults.effects]);
    appliedSettingsRevision.current = revision;
  }, [centralOutputSettings, cloneFormDirty, effectsDirty, getValues, moduleDefaults.cloningLanguage, moduleDefaults.cloningQuality, moduleDefaults.effects, moduleDefaults.providerId, moduleDefaults.voiceCloningProviderId, reset, resetClone, settingsQuery.data?.profile.revision, tuningDirty, voiceFormDirty]);

  const scriptText = watch('text') ?? '';
  const scriptSegments = useMemo(() => parseScriptSegments(scriptText), [scriptText]);
  const parsedSpeakers = useMemo(() => parseScriptSpeakers(scriptText), [scriptText]);
  const ttsProviders = useMemo(() => ttsCapableProviders(providersQuery.data), [providersQuery.data]);
  const cloneProviders = useMemo(() => cloneCapableProviders(providersQuery.data), [providersQuery.data]);
  const queriedVoiceJobs = useMemo(() => jobsQuery.data?.jobs.filter((job) => job.module === 'voice' || job.module === 'voice-cloning') ?? [], [jobsQuery.data?.jobs]);
  const audioAssets = assetsQuery.data?.assets.filter((asset) => asset.type === 'audio' || asset.type === 'voice_profile') ?? [];
  const profileAssets = audioAssets.filter((asset) => asset.type === 'voice_profile');
  const generatedAudioAssets = audioAssets.filter((asset) => asset.type === 'audio');
  const latestResultAsset = firstResultAsset(generatedAudioAssets);
  const filteredProfileAssets = useMemo(
    () => profileAssets.filter((asset) => voiceAssetName(asset).toLowerCase().includes(voiceSearch.trim().toLowerCase()) || voiceProfileName(asset).toLowerCase().includes(voiceSearch.trim().toLowerCase())),
    [profileAssets, voiceSearch],
  );
  const visibleProfileAssets = showAllVoices ? filteredProfileAssets : filteredProfileAssets.slice(0, 6);
  const selectedCloneSample = cloneSource === 'record' ? recordedSample : sampleFile;
  const selectedCloneSampleName = cloneSource === 'record' ? 'recorded-voice.webm' : sampleFile?.name ?? null;
  const defaultTtsProviderId = moduleDefaults.providerId || ttsProviders[0]?.id || '';
  const defaultCloneProviderId = moduleDefaults.voiceCloningProviderId || cloneProviders[0]?.id || '';
  const defaultVoiceId = profileAssets[0] ? voiceReference(profileAssets[0]) : '';
  const defaultSpeakerName = parsedSpeakers[0]?.name ?? 'Narrator';

  const createJobMutation = useMutation({
    mutationFn: (values: VoiceFormValues) =>
      omnixApiClient.createJob({
        module: 'voice',
        type: parsedSpeakers.length > 1 ? 'tts.multi_speaker_synthesize' : 'tts.synthesize',
        resource_class: 'gpu:tts',
        priority: 0,
        input_payload: {
          text: values.text,
          provider_id: values.providerId || defaultTtsProviderId || null,
          language: moduleDefaults.language || null,
          speaker: values.speaker || defaultSpeakerName || null,
          voice_id: assignedVoiceFor(parsedSpeakers[0] ?? { name: '', count: 0 }, profileAssets, speakerVoiceAssignments) || values.voiceId || defaultVoiceId || null,
          script_mode: parsedSpeakers.length > 1 ? 'multi_speaker' : 'single_speaker',
          script_speakers: parsedSpeakers,
          script_segments: scriptSegments,
          character_voice_assignments: buildSpeakerAssignments(parsedSpeakers, profileAssets, speakerVoiceAssignments, speakerStyleAssignments),
          output_settings: outputSettings,
          audio_effects: enabledEffects,
          save_output: true,
        },
        stages: voiceSynthesisStages(scriptSegments),
      }),
    onMutate: () => {
      setSelectedOutputKey('');
      setPendingPlaybackJobId('');
      audioRef.current?.pause();
      setIsPlaying(false);
    },
    onSuccess: async (job) => {
      const output = extractPlayableOutputs([job])[0];
      if (output) {
        setCompletedPlaybackJob(job);
        setSelectedOutputKey(output.key);
      } else if (job.status !== 'failed') {
        setPendingPlaybackJobId(job.id);
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }),
        queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] }),
      ]);
    },
  });

  const previewVoiceMutation = useMutation({
    mutationFn: (asset: VoiceAsset) => {
      const voiceId = voiceReference(asset);
      const voiceName = voiceAssetName(asset);
      return omnixApiClient.createJob({
        module: 'voice',
        type: 'tts.synthesize',
        resource_class: 'gpu:tts',
        priority: 1,
        input_payload: {
          text: `This is a preview of ${voiceName} speaking in Voice Studio.`,
          provider_id: defaultTtsProviderId || null,
          language: moduleDefaults.language || null,
          speaker: voiceName,
          voice_id: voiceId,
          script_mode: 'single_speaker',
          script_speakers: [{ name: 'Preview', count: 1 }],
          script_segments: [{ index: 0, speaker: 'Preview', text: `This is a preview of ${voiceName} speaking in Voice Studio.` }],
          character_voice_assignments: [{ speaker: 'Preview', voice_id: voiceId, style: 'Neutral', line_count: 1 }],
          output_settings: outputSettings,
          audio_effects: enabledEffects,
          save_output: true,
        },
        stages: voiceSynthesisStages([{ index: 0, speaker: 'Preview', text: 'Preview voice.' }]),
      });
    },
    onSuccess: async (job) => {
      selectFirstJobOutput(job, setSelectedOutputKey);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }),
        queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] }),
      ]);
    },
  });

  const cloneJobMutation = useMutation({
    mutationFn: async (values: VoiceCloneFormValues) => {
      const sample = cloneSource === 'record' ? recordedSample : sampleFile;
      if (!sample) {
        throw new Error('Upload or record an audio sample before creating a clone.');
      }
      const job = await omnixApiClient.createJob({
        module: 'voice-cloning',
        type: 'voice-cloning.create-profile',
        resource_class: 'gpu:tts',
        priority: 0,
        input_payload: {
          profile_name: values.profileName,
          provider_id: values.providerId || defaultCloneProviderId || null,
          language: values.language || moduleDefaults.cloningLanguage,
          quality: values.quality || moduleDefaults.cloningQuality,
          notes: values.notes,
          reference_text: values.referenceText.trim(),
          generate_transcript: values.generateTranscript,
          stt_provider_id: moduleDefaults.sttProviderId || null,
          source_kind: cloneSource,
          source_file_name: selectedCloneSampleName,
          source_file_size: sample.size,
          source_mime_type: sample.type || null,
          sample_audio_base64: await blobToDataUrl(sample),
          storage_hint: 'resources/voice_clones',
        },
        stages: [
          { id: 'capture-sample', label: cloneSource === 'record' ? 'Record voice sample' : 'Ingest uploaded audio', resource_class: 'cpu', status: 'queued' },
          ...(values.generateTranscript && !values.referenceText.trim()
            ? [{ id: 'transcribe-sample', label: 'Generate reference transcript', resource_class: 'gpu:stt' as const, status: 'queued' as const }]
            : []),
          { id: 'build-profile', label: 'Create voice profile', resource_class: 'gpu:tts', status: 'queued' },
          { id: 'preview', label: 'Generate preview clip', resource_class: 'gpu:tts', status: 'queued' },
          { id: 'store-profile', label: 'Store local voice clone', resource_class: 'cpu', status: 'queued' },
        ],
      });
      if (job.status === 'failed') {
        throw new Error(job.error?.message || 'The voice clone could not be created.');
      }
      return job;
    },
    onSuccess: async (_job, values) => {
      resetClone({ providerId: values.providerId, profileName: '', language: values.language, quality: values.quality, notes: '', referenceText: '', generateTranscript: values.generateTranscript });
      setSampleFile(null);
      setRecordedSample(null);
      setRecordingStatus(_job.status === 'completed' ? 'Voice clone created and added to the Voice Library.' : 'Voice clone queued. It will appear in the Voice Library when complete.');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }),
        queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] }),
      ]);
    },
  });

  const deleteVoiceMutation = useMutation({
    mutationFn: (asset: VoiceAsset) => omnixApiClient.deleteVoiceAsset(voiceAssetId(asset)),
    onSuccess: async (_result, asset) => {
      const deletedIds = new Set([voiceAssetId(asset)]);
      if (deletedIds.has(getValues('voiceId'))) {
        setValue('voiceId', '');
        setValue('speaker', '');
      }
      setSpeakerVoiceAssignments((current) => Object.fromEntries(Object.entries(current).filter(([, voiceId]) => !deletedIds.has(voiceId))));
      setSaveMessage(`${voiceAssetName(asset)} was deleted from the Voice Library.`);
      await queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] });
    },
  });

  const transcribeSampleMutation = useMutation({
    mutationFn: async () => {
      const sample = cloneSource === 'record' ? recordedSample : sampleFile;
      if (!sample) {
        throw new Error('Upload or record an audio sample before transcribing it.');
      }
      const values = getCloneValues();
      const job = await omnixApiClient.createJob({
        module: 'voice-cloning',
        type: 'voice-cloning.transcribe-sample',
        resource_class: 'gpu:stt',
        priority: 0,
        input_payload: {
          provider_id: values.providerId || defaultCloneProviderId || null,
          stt_provider_id: moduleDefaults.sttProviderId || null,
          language: values.language || moduleDefaults.cloningLanguage,
          source_file_name: selectedCloneSampleName,
          source_mime_type: sample.type || null,
          sample_audio_base64: await blobToDataUrl(sample),
        },
        stages: [{ id: 'transcribe-sample', label: 'Transcribe reference sample', resource_class: 'gpu:stt', status: 'queued' }],
      });
      if (job.status === 'failed') {
        throw new Error(job.error?.message || 'STT could not transcribe the audio sample.');
      }
      const transcript = transcriptFromJob(job);
      if (!transcript) {
        throw new Error('STT completed without returning a transcript.');
      }
      return transcript;
    },
    onSuccess: (transcript) => {
      setCloneValue('referenceText', transcript, { shouldDirty: true, shouldValidate: true });
      setCloneValue('generateTranscript', false, { shouldDirty: true });
      setRecordingStatus('Transcript generated. Review or correct it before creating the clone.');
    },
  });

  const voiceJobs = useMemo(
    () => mergeVoiceJobs([...queriedVoiceJobs, createJobMutation.data, previewVoiceMutation.data, cloneJobMutation.data, pendingPlaybackJobQuery.data, completedPlaybackJob ?? undefined]),
    [cloneJobMutation.data, completedPlaybackJob, createJobMutation.data, pendingPlaybackJobQuery.data, previewVoiceMutation.data, queriedVoiceJobs],
  );
  const filteredVoiceJobs = jobQueueFilter === 'active' ? activeJobs(voiceJobs) : jobQueueFilter === 'failed' ? voiceJobs.filter((job) => job.status === 'failed') : voiceJobs.filter((job) => job.status !== 'queued' && job.status !== 'running' && job.status !== 'leased');
  const playableOutputs = useMemo(() => extractPlayableOutputs(voiceJobs), [voiceJobs]);
  const isPendingSpeechOutput = createJobMutation.isPending || Boolean(pendingPlaybackJobId);
  const currentOutput = isPendingSpeechOutput ? null : playableOutputs.find((output) => output.key === selectedOutputKey) ?? playableOutputs[0] ?? null;
  const currentOutputTitle = isPendingSpeechOutput ? 'Generating new speech…' : currentOutput?.title ?? (latestResultAsset ? voiceAssetName(latestResultAsset) : `${module.label} output`);
  const latestPreviewTitle = isPendingSpeechOutput ? 'Waiting for the new speech output…' : currentOutput?.title ?? (latestResultAsset ? voiceAssetName(latestResultAsset) : 'No generated audio yet');
  const effectiveDuration = playbackDuration || currentOutput?.duration || estimateDurationFromText(scriptText);
  const createJobFailureMessage = voiceJobErrorMessage(createJobMutation.data);
  const previewFailureMessage = voiceJobErrorMessage(previewVoiceMutation.data);
  const voiceGenerationFailure = createJobFailureMessage || previewFailureMessage;

  useEffect(() => {
    if (!pendingPlaybackJobId || !pendingPlaybackJobQuery.data) return;
    const job = pendingPlaybackJobQuery.data;
    if (job.status === 'failed') {
      setPendingPlaybackJobId('');
      setSaveMessage(voiceJobErrorMessage(job));
      return;
    }
    const output = extractPlayableOutputs([job])[0];
    if (!output) return;
    setCompletedPlaybackJob(job);
    setSelectedOutputKey(output.key);
    setPendingPlaybackJobId('');
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] }),
      queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] }),
    ]);
  }, [pendingPlaybackJobId, pendingPlaybackJobQuery.data, queryClient]);

  useEffect(() => {
    if (playableOutputs.length > 0 && !playableOutputs.some((output) => output.key === selectedOutputKey)) {
      setSelectedOutputKey(playableOutputs[0].key);
    }
  }, [playableOutputs, selectedOutputKey]);

  useEffect(() => {
    setPlaybackTime(0);
    setPlaybackDuration(currentOutput?.duration ?? 0);
    setIsPlaying(false);
    audioRef.current?.pause();
  }, [currentOutput?.key]);

  async function togglePlayback() {
    const audio = audioRef.current;
    if (!audio || !currentOutput) {
      setSaveMessage('Generate speech before playing audio.');
      return;
    }
    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
      return;
    }
    try {
      await audio.play();
      setIsPlaying(true);
    } catch (error) {
      setSaveMessage(error instanceof Error ? error.message : 'Audio playback could not start.');
    }
  }

  function selectOutputOffset(offset: number) {
    if (!playableOutputs.length) {
      return;
    }
    const index = Math.max(0, playableOutputs.findIndex((output) => output.key === currentOutput?.key));
    const nextIndex = (index + offset + playableOutputs.length) % playableOutputs.length;
    setSelectedOutputKey(playableOutputs[nextIndex].key);
  }

  function selectJobOutput(jobId: string) {
    const output = playableOutputs.find((entry) => entry.jobId === jobId);
    if (output) {
      setSelectedOutputKey(output.key);
      setSaveMessage(`Selected ${output.title} for playback.`);
    }
  }

  function seekPlayback(value: number) {
    const audio = audioRef.current;
    setPlaybackTime(value);
    if (audio) {
      audio.currentTime = value;
    }
  }

  function downloadCurrentOutput() {
    if (!currentOutput || typeof document === 'undefined') {
      setSaveMessage('Generate speech before saving output.');
      return;
    }
    downloadUrl(currentOutput.dataUrl, `${safeDownloadName(currentOutput.title)}.wav`);
  }

  async function toggleRecording() {
    if (mediaRecorder && isRecording) {
      mediaRecorder.stop();
      setIsRecording(false);
      return;
    }
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setRecordingStatus('Browser recording is not available. Upload an audio file instead.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      };
      recorder.onstop = () => {
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        setRecordedSample(blob);
        setRecordingStatus(`Recorded sample · ${formatBytes(blob.size)}`);
        setMediaRecorder(null);
        setIsRecording(false);
        stream.getTracks().forEach((track) => track.stop());
      };
      setRecordedSample(null);
      setMediaRecorder(recorder);
      setIsRecording(true);
      setRecordingStatus('Recording… click Record Voice again to stop.');
      recorder.start();
    } catch (error) {
      setRecordingStatus(error instanceof Error ? error.message : 'Could not start recording.');
      setIsRecording(false);
    }
  }

  function updateOutputSetting(name: OutputSettingName, value: number) {
    setTuningDirty(true);
    setOutputSettings((current) => ({ ...current, [name]: value }));
  }

  function resetTuningDefaults() {
    setOutputSettings(centralOutputSettings);
    setTuningDirty(false);
  }

  function resetEffectDefaults() {
    setEnabledEffects([...moduleDefaults.effects]);
    setEffectsDirty(false);
  }

  function resetEmbeddedCloneDefaults() {
    resetClone({ providerId: moduleDefaults.voiceCloningProviderId, profileName: '', language: moduleDefaults.cloningLanguage, quality: moduleDefaults.cloningQuality, notes: '', referenceText: '', generateTranscript: true });
    setSampleFile(null);
    setRecordedSample(null);
    setRecordingStatus('');
    setCloneSource('upload');
  }

  function requestVoiceDelete(asset: VoiceAsset) {
    const name = voiceAssetName(asset);
    if (typeof window !== 'undefined' && !window.confirm(`Delete “${name}”? This permanently removes the cloned voice audio and metadata.`)) return;
    deleteVoiceMutation.mutate(asset);
  }

  return (
    <WorkspacePanel label={module.label}>
      <div className="voice-studio-app">
        <main className="voice-workspace-final">
          <header className="voice-final-header">
            <div><Title order={2}>Voice Studio</Title><Text size="sm">Clone voices, manage your voice library, and generate natural speech with advanced controls.</Text></div>
            <TtsEnginePicker />
          </header>

          <div className="voice-top-grid">
            <section className="voice-panel-final clone-panel">
              <Title order={4}>Clone a Voice</Title>
              <Text size="sm">Create a high-quality clone from your audio.</Text>
              <form className="voice-clone-form" onSubmit={handleCloneSubmit((values) => cloneJobMutation.mutate(values))}>
                <div className="clone-source-grid">
                  <button className={cloneSource === 'upload' ? 'active' : ''} type="button" onClick={() => setCloneSource('upload')}><strong>Upload Audio</strong><small>WAV, MP3, MP4</small><small>Min. 1 min recommended</small></button>
                  <button className={cloneSource === 'record' ? 'active' : ''} type="button" onClick={() => { setCloneSource('record'); void toggleRecording(); }}><strong>{isRecording ? 'Stop Recording' : 'Record Voice'}</strong><small>Record directly</small><small>in your browser</small></button>
                </div>
                <label>Voice Name<input aria-invalid={Boolean(cloneErrors.profileName)} placeholder="e.g. My New Voice" {...registerClone('profileName', { required: true })} /></label>
                <label className="voice-file-field">Audio sample<input type="file" accept="audio/*" onChange={(event) => { setSampleFile(event.currentTarget.files?.[0] ?? null); setRecordedSample(null); setCloneSource('upload'); }} /><small>{cloneSource === 'record' ? recordingStatus || 'No recording yet.' : sampleFile ? `${sampleFile.name} · ${formatBytes(sampleFile.size)}` : 'No sample selected yet.'}</small></label>
                <div className="voice-two-col"><label>Language / Accent<input {...registerClone('language')} /></label><label>Quality<select {...registerClone('quality')}><option>High</option><option>Standard</option><option>Draft</option><option>High (Recommended)</option><option>Balanced</option><option>Fast Preview</option></select></label></div>
                <label>Reference transcript (optional)<textarea rows={3} placeholder="Paste the exact spoken words, or transcribe the selected sample with STT." {...registerClone('referenceText')} /></label>
                <Group className="voice-transcribe-action" justify="space-between" align="center"><Text size="xs">Use STT now so you can verify the exact words before cloning.</Text><Button type="button" size="sm" variant="outline" loading={transcribeSampleMutation.isPending} disabled={!selectedCloneSample || transcribeSampleMutation.isPending} onClick={() => transcribeSampleMutation.mutate()}>Generate Transcript</Button></Group>
                {transcribeSampleMutation.isError ? <div className="platform-empty" role="alert">{transcribeSampleMutation.error instanceof Error ? transcribeSampleMutation.error.message : 'STT could not transcribe the sample.'}</div> : null}
                <label className="voice-transcript-option"><input type="checkbox" {...registerClone('generateTranscript')} /><span>Generate transcript with STT<small>Uses the configured STT provider when the reference transcript is empty.</small></span></label>
                <label>Notes / Tags (optional)<textarea rows={2} placeholder="Add notes or tags to help identify this voice..." {...registerClone('notes')} /></label>
                <Group justify="space-between"><Text size="xs">Clones are stored to Omnix: /resources/voice_clones</Text><Group gap="xs"><Button type="button" variant="subtle" onClick={resetEmbeddedCloneDefaults}>Reset defaults</Button><Button type="submit" loading={cloneJobMutation.isPending} disabled={!selectedCloneSample || cloneJobMutation.isPending}>Create Clone</Button></Group></Group>
              </form>
              <FeatureValidationMessage show={Boolean(cloneErrors.profileName)} message="Enter a voice name before creating a clone." />
              <FeatureSubmitFeedback error={cloneJobMutation.error} errorPrefix="Voice clone request" isError={cloneJobMutation.isError} isPending={cloneJobMutation.isPending} jobId={cloneJobMutation.data?.status === 'failed' ? undefined : cloneJobMutation.data?.id} pendingMessage="Queueing voice clone job…" successPrefix="Voice clone job queued" />
            </section>

            <section className="voice-panel-final library-panel-final">
              <Group justify="space-between"><div><Title order={4}>Voice Library</Title><Text size="sm">Your cloned voices stored in Omnix resources.</Text></div><Button aria-label="Refresh voice library" size="xs" variant="subtle" loading={assetsQuery.isFetching} onClick={() => void assetsQuery.refetch()}>Refresh ⟳</Button></Group>
              <label className="voice-search"><span>Search voices</span><input aria-label="Search voices" value={voiceSearch} onChange={(event) => setVoiceSearch(event.currentTarget.value)} placeholder="Search voices..." /></label>
              <div role="group" className="voice-library-table" aria-label="Voice library">
                <div className="voice-library-row table-head"><span>Name</span><span>ID / Prefix</span><span>Status</span><span>Actions</span></div>
                {assetsQuery.isLoading ? <div className="platform-empty" role="status">Loading cloned voices…</div> : assetsQuery.isError ? <div className="platform-empty" role="alert">Voice Library failed to load. The local asset index may be unavailable.<Button aria-label="Retry voice library" size="xs" variant="subtle" onClick={() => void assetsQuery.refetch()}>Retry</Button></div> : visibleProfileAssets.length ? visibleProfileAssets.map((asset) => <VoiceLibraryRow asset={asset} deleting={Boolean(deleteVoiceMutation.isPending && deleteVoiceMutation.variables && voiceAssetId(deleteVoiceMutation.variables) === voiceAssetId(asset))} key={voiceAssetId(asset)} onDelete={() => requestVoiceDelete(asset)} onPreview={() => previewVoiceMutation.mutate(asset)} onUse={() => useVoice(asset, setValue, setSaveMessage)} />) : <div className="platform-empty" role="status">No cloned voices were indexed. Create a clone or refresh the library to rescan local voice files.</div>}
              </div>
              {deleteVoiceMutation.isError ? <div className="platform-empty" role="alert">Voice deletion failed. The voice was not removed.</div> : null}
              <Group justify="space-between"><Text size="xs">{filteredProfileAssets.length} voices</Text><Button size="xs" variant="subtle" onClick={() => setShowAllVoices((value) => !value)}>{showAllVoices ? 'Show first 6' : 'View all voices →'}</Button></Group>
            </section>

            <section className="voice-panel-final queue-panel-final">
              <Title order={4}>Jobs & Playback Queue</Title>
              <Text size="sm">Monitor synthesis jobs and replay results.</Text>
              <div className="queue-tabs"><button className={jobQueueFilter === 'active' ? 'active' : ''} type="button" aria-pressed={jobQueueFilter === 'active'} onClick={() => setJobQueueFilter('active')}>Active ({activeJobs(voiceJobs).length})</button><button className={jobQueueFilter === 'recent' ? 'active' : ''} type="button" aria-pressed={jobQueueFilter === 'recent'} onClick={() => setJobQueueFilter('recent')}>Recent ({voiceJobs.filter((job) => job.status !== 'queued' && job.status !== 'running' && job.status !== 'leased').length})</button><button className={jobQueueFilter === 'failed' ? 'active' : ''} type="button" aria-pressed={jobQueueFilter === 'failed'} onClick={() => setJobQueueFilter('failed')}>Failed ({voiceJobs.filter((job) => job.status === 'failed').length})</button></div>
              <div className="queue-list-final">{(voiceJobs.length ? filteredVoiceJobs : demoJobs()).slice(0, 4).map((job) => <QueueRow job={job} key={job.id} onSelect={() => selectJobOutput(job.id)} selected={playableOutputs.some((output) => output.jobId === job.id && output.key === currentOutput?.key)} />)}</div>
              <div className="latest-preview-row"><Button size="xs" variant="subtle" onClick={() => void togglePlayback()}>{isPlaying ? 'Ⅱ' : '▶'}</Button><Waveform /><Text size="xs">{latestPreviewTitle}</Text></div>
            </section>
          </div>

          <section className="voice-panel-final tts-panel-final">
            <Group justify="space-between"><div><Title order={4}>Text-to-Speech (Multi-Voice)</Title><Text size="sm">Write your script with character tags, AI will detect speakers and you can assign voices and styles before generating speech.</Text></div><div className="script-actions"><Button variant="subtle" onClick={() => setValue('text', '')}>Clear</Button><Button variant="subtle" onClick={() => loadScript(setValue, setSaveMessage)}>Load Script</Button><Button onClick={() => saveScript(scriptText, setSaveMessage)}>Save Script</Button></div></Group>
            <form className="tts-workflow-grid" onSubmit={handleSubmit((values) => createJobMutation.mutate(values))}>
              <section className="script-card"><Group justify="space-between"><b>1. Script <small>(use character tags)</small></b><small>ⓘ How it works</small></Group><textarea aria-label="Script" rows={8} {...register('text', { required: true })} /><Group justify="space-between"><Text size="xs">{scriptSegments.length} stages · {parsedSpeakers.length} speakers detected</Text><Button size="xs" type="button" variant="subtle" onClick={() => setSaveMessage(`${parsedSpeakers.length} speaker${parsedSpeakers.length === 1 ? '' : 's'} detected: ${parsedSpeakers.map((speaker) => speaker.name).join(', ')}`)}>Detect Characters</Button></Group>{parsedSpeakers.length ? <div className="voice-success-note">AI automatically detected {parsedSpeakers.length} character{parsedSpeakers.length === 1 ? '' : 's'} from your script.</div> : null}{saveMessage ? <div className="voice-success-note">{saveMessage}</div> : null}</section>
              <section className="assignment-card"><Group justify="space-between"><b>2. Detected Characters & Voice Assignment</b><OmnixStatusPill>{parsedSpeakers.length} detected</OmnixStatusPill></Group><div className="assignment-table"><div className="assignment-row assignment-head"><span>Character</span><span>Assign Voice</span><span>Style / Emotion</span><span>Preview</span></div>{parsedSpeakers.map((speaker, index) => <AssignmentRow assets={profileAssets} index={index} key={speaker.name} speaker={speaker} voiceValue={assignedVoiceFor(speaker, profileAssets, speakerVoiceAssignments)} styleValue={speakerStyleAssignments[speaker.name] ?? STYLE_OPTIONS[Math.min(index, STYLE_OPTIONS.length - 1)]} onPreview={(voiceId) => previewVoiceById(voiceId, profileAssets, previewVoiceMutation.mutate, setSaveMessage)} onVoiceChange={(voiceId) => setSpeakerVoiceAssignments((current) => ({ ...current, [speaker.name]: voiceId }))} onStyleChange={(style) => setSpeakerStyleAssignments((current) => ({ ...current, [speaker.name]: style }))} />)}</div><Text size="xs">Unlabeled scripts use a single Narrator speaker. Tagged scripts are generated one stage per line.</Text></section>
              <section className="generate-card"><b>3. Generate Speech</b><Text size="sm">Generate multi-stage audio from your script.</Text><Button className="generate-speech-button" type="submit" loading={createJobMutation.isPending}>▥ Generate Speech</Button><Button type="button" variant="subtle" onClick={downloadCurrentOutput}>⇩ Save Output</Button><Text size="xs">Estimated duration: ~ {formatPlaybackTime(estimateDurationFromText(scriptText))}</Text><FeatureSubmitFeedback error={createJobMutation.error} errorPrefix="TTS request" isError={createJobMutation.isError} isPending={createJobMutation.isPending} jobId={createJobMutation.data?.status === 'failed' ? undefined : createJobMutation.data?.id} pendingMessage="Queueing TTS job…" successPrefix="TTS job queued" /></section>
            </form>
            <FeatureValidationMessage show={Boolean(errors.text)} message="Enter script text before generating speech." />
            <FeatureValidationMessage show={Boolean(voiceGenerationFailure)} message={voiceGenerationFailure || ''} />
          </section>

          <div className="voice-bottom-grid">
            <section className="voice-panel-final enhancement-panel"><Group justify="space-between"><div><Title order={5}>Voice Enhancement</Title><Text size="xs">Fine-tune and enhance the output with advanced controls.</Text></div><Button size="xs" type="button" variant="subtle" onClick={resetTuningDefaults}>Reset tuning</Button></Group><div className="enhancement-controls">{(Object.entries(outputSettings) as [OutputSettingName, number][]).map(([name, value]) => { const unused = Boolean(selectedEngine && !selectedEngine.voice_controls.includes(name)); return <label key={name} title={unused ? `${selectedEngine?.label} has no equivalent setting; this slider is ignored.` : settingDescription(name)} className={unused ? 'enhancement-unused' : undefined}><span>{settingLabel(name)}</span><b>{unused ? 'Not used' : settingValueLabel(name, value)}</b><input aria-label={`Output ${name}`} type="range" min={rangeMin(name)} max={rangeMax(name)} step={rangeStep(name)} value={value} disabled={unused} onChange={(event) => updateOutputSetting(name, Number(event.currentTarget.value))} /></label>; })}</div></section>
            <section className="voice-panel-final effects-panel"><Group justify="space-between"><div><Title order={5}>Audio Effects</Title><Text size="xs">Apply effects to polish and enhance the final audio.</Text></div><Button size="xs" type="button" variant="subtle" onClick={resetEffectDefaults}>Reset effects</Button></Group><div className="effect-buttons">{AUDIO_EFFECTS.map((effect) => <button className={enabledEffects.includes(effect) ? 'active' : ''} key={effect} type="button" onClick={() => { setEffectsDirty(true); toggleEffect(effect, setEnabledEffects); }}>{effect}</button>)}</div></section>
          </div>

          <footer className="now-playing-bar">
            <audio ref={audioRef} src={currentOutput?.dataUrl ?? undefined} preload="metadata" onLoadedMetadata={(event) => setPlaybackDuration(event.currentTarget.duration || currentOutput?.duration || 0)} onTimeUpdate={(event) => setPlaybackTime(event.currentTarget.currentTime)} onEnded={() => setIsPlaying(false)} />
            <button type="button">⌃</button><div><b>Now Playing</b><span>{currentOutputTitle} · {parsedSpeakers.length} speaker{parsedSpeakers.length === 1 ? '' : 's'}</span></div><button type="button" onClick={() => selectOutputOffset(-1)}>↢</button><button className="main-play" type="button" onClick={() => void togglePlayback()}>{isPlaying ? 'Ⅱ' : '▶'}</button><button type="button" onClick={() => selectOutputOffset(1)}>↣</button><span>{formatPlaybackTime(playbackTime)}</span><input aria-label="Voice playback position" className="now-playing-seek" type="range" min={0} max={Math.max(effectiveDuration, 0.1)} step="0.01" value={Math.min(playbackTime, effectiveDuration)} onChange={(event) => seekPlayback(Number(event.currentTarget.value))} /><span>{formatPlaybackTime(effectiveDuration)}</span><button type="button" onClick={downloadCurrentOutput}>⇩</button><button type="button">⋯</button></footer>
        </main>
      </div>
    </WorkspacePanel>
  );
}

function VoiceLibraryRow({ asset, deleting, onDelete, onPreview, onUse }: { asset: VoiceAsset; deleting: boolean; onDelete: () => void; onPreview: () => void; onUse: () => void }) {
  return <div className="voice-library-row"><span><i>{voiceInitial(asset)}</i><b>{voiceAssetName(asset)}</b><small>{voiceProfileDescription(asset)}</small></span><span title={voiceProfileName(asset)}>{voiceProfileName(asset)}</span><span className="ready-chip">Ready</span><span className="voice-library-actions"><Button aria-label={`Preview ${voiceAssetName(asset)}`} size="xs" variant="subtle" onClick={onPreview}>Preview</Button><Button size="xs" variant="subtle" onClick={onUse}>Use</Button><Button aria-label={`Delete ${voiceAssetName(asset)}`} color="red" loading={deleting} size="xs" variant="outline" onClick={onDelete}>Delete</Button></span></div>;
}

function QueueRow({ job, onSelect, selected }: { job: { id: string; type: string; status: string; module: string; progress?: { current?: number; total?: number }; stages?: Array<{ label?: string; status?: string }> }; onSelect?: () => void; selected?: boolean }) {
  const progress = jobProgressPercent(job.progress);
  const stageSummary = job.stages?.length ? `${job.stages.length} stages · ${job.stages.slice(0, 2).map((stage) => stage.label || stage.status || 'stage').join(', ')}` : job.module;
  return <article className={selected ? 'queue-row-final selected' : 'queue-row-final'}><span className="job-icon">▥</span><div><b>{job.type}</b><small>{stageSummary}</small></div><div><OmnixStatusPill>{job.status}</OmnixStatusPill>{progress ? <div className="queue-progress"><span style={{ width: `${progress}%` }} /></div> : null}</div><small>{progress || job.status === 'completed' ? `${progress}%` : '—'}</small><button type="button" onClick={onSelect}>▶</button></article>;
}

function AssignmentRow({ assets, index, speaker, voiceValue, styleValue, onPreview, onVoiceChange, onStyleChange }: { assets: VoiceAsset[]; index: number; speaker: ScriptSpeakerRow; voiceValue: string; styleValue: string; onPreview: (voiceId: string) => void; onVoiceChange: (voiceId: string) => void; onStyleChange: (style: string) => void }) {
  return <div className="assignment-row"><span><i>{speaker.name.slice(0, 2).toUpperCase()}</i>{speaker.name}</span><select aria-label={`${speaker.name} voice`} value={voiceValue} onChange={(event) => onVoiceChange(event.currentTarget.value)}>{assets.map((asset) => <option key={voiceAssetId(asset)} value={voiceReference(asset)}>{voiceAssetName(asset)} ({voiceProfileName(asset)})</option>)}{!assets.length ? <option value="">No cloned voices</option> : null}</select><select aria-label={`${speaker.name} style`} value={styleValue} onChange={(event) => onStyleChange(event.currentTarget.value)}>{STYLE_OPTIONS.map((style) => <option key={style} value={style}>{style}</option>)}</select><Button size="xs" variant="subtle" type="button" onClick={() => onPreview(voiceValue)}>▶</Button></div>;
}

function Waveform() {
  return <div className="voice-waveform-final" aria-hidden="true">{Array.from({ length: 64 }, (_, index) => <span key={index} style={{ height: `${18 + ((index * 19) % 62)}%` }} />)}</div>;
}

function ttsCapableProviders(payload: ProviderFacadePayload | undefined) {
  return payload?.providers.filter((provider) => provider.capabilities.includes('tts')) ?? [];
}

function cloneCapableProviders(payload: ProviderFacadePayload | undefined) {
  return payload?.providers.filter((provider) => provider.capabilities.includes('voice_cloning') || provider.capabilities.includes('tts')) ?? [];
}

function buildSpeakerAssignments(speakers: ScriptSpeakerRow[], assets: VoiceAsset[], voiceAssignments: Record<string, string>, styleAssignments: Record<string, string>) {
  return speakers.map((speaker, index) => ({
    speaker: speaker.name,
    voice_id: assignedVoiceFor(speaker, assets, voiceAssignments) || null,
    style: styleAssignments[speaker.name] ?? STYLE_OPTIONS[Math.min(index, STYLE_OPTIONS.length - 1)],
    line_count: speaker.count,
  }));
}

function assignedVoiceFor(speaker: ScriptSpeakerRow, assets: VoiceAsset[], voiceAssignments: Record<string, string>): string {
  return voiceAssignments[speaker.name] ?? voiceReference(findMatchingVoice(speaker.name, assets)) ?? voiceReference(assets[0]) ?? '';
}

function findMatchingVoice(name: string, assets: VoiceAsset[]): VoiceAsset | undefined {
  const normalizedName = name.toLowerCase();
  return assets.find((asset) => voiceAssetName(asset).toLowerCase().includes(normalizedName) || voiceProfileName(asset).toLowerCase().includes(normalizedName));
}

function previewVoiceById(voiceId: string, assets: VoiceAsset[], preview: (asset: VoiceAsset) => void, setSaveMessage: (message: string) => void) {
  // Older saved assignments name the voice's file path.
  const fileName = voiceId.split(/[\/]/).pop();
  const asset = assets.find((entry) => voiceReference(entry) === voiceId || (Boolean(fileName) && voiceFileName(entry) === fileName));
  if (asset) {
    preview(asset);
  } else {
    setSaveMessage('Select a cloned voice before previewing.');
  }
}

function useVoice(asset: VoiceAsset, setValue: ReturnType<typeof useForm<VoiceFormValues>>['setValue'], setSaveMessage: (message: string) => void) {
  setValue('voiceId', voiceReference(asset));
  setValue('speaker', voiceAssetName(asset));
  setSaveMessage(`Selected ${voiceAssetName(asset)} for synthesis.`);
}

function activeJobs<T extends { status: string }>(jobs: T[]): T[] {
  return jobs.filter((job) => job.status === 'queued' || job.status === 'running' || job.status === 'leased');
}

function mergeVoiceJobs(jobs: Array<JobRecord | undefined>): JobRecord[] {
  const merged = new Map<string, JobRecord>();
  for (const job of jobs) {
    if (job?.id) {
      merged.set(job.id, job);
    }
  }
  return Array.from(merged.values());
}

function extractPlayableOutputs(jobs: JobRecord[]): PlayableVoiceOutput[] {
  const outputs: PlayableVoiceOutput[] = [];
  for (const job of jobs) {
    for (const ref of jobOutputRefs(job)) {
      if (isPlayableAudioRef(ref)) {
        const title = ref.title || job.type || 'voice_output';
        outputs.push({ dataUrl: playableAudioUrl(ref), duration: Number(ref.duration || 0), jobId: job.id, key: `${job.id}:${ref.asset_id || ref.title || outputs.length}`, title });
      }
    }
  }
  return outputs;
}

function transcriptFromJob(job: JobRecord): string {
  const transcript = jobOutputRefs(job).find((ref) => ref.type === 'transcript' && typeof ref.content === 'string')?.content;
  return typeof transcript === 'string' ? transcript.trim() : '';
}

// New outputs reference the stored asset (audio_url); older job rows embed
// the audio as a data URL.
function playableAudioUrl(ref: JobOutputRef): string {
  if (typeof ref.audio_url === 'string' && ref.audio_url.startsWith('/api/assets/')) return ref.audio_url;
  if (typeof ref.data_url === 'string' && ref.data_url.startsWith('data:audio/')) return ref.data_url;
  return '';
}

function isPlayableAudioRef(ref: JobOutputRef): boolean {
  return playableAudioUrl(ref) !== '' && !isFallbackOutputRef(ref);
}

function selectFirstJobOutput(job: JobRecord, setSelectedOutputKey: (key: string) => void): void {
  const output = extractPlayableOutputs([job])[0];
  if (output) {
    setSelectedOutputKey(output.key);
  }
}

function voiceJobErrorMessage(job: JobRecord | undefined): string {
  if (!job || job.status !== 'failed') {
    return '';
  }
  const message = typeof job.error?.message === 'string' ? job.error.message : 'Voice Studio job failed.';
  return `Voice Studio failed: ${message}`;
}

function demoJobs() {
  return [
    { id: 'demo-active', type: 'meeting_script_v2', status: 'running', module: 'Multi-voice synthesis', progress: { current: 65, total: 100 } },
    { id: 'demo-queued', type: 'character_demo', status: 'queued', module: 'Multi-voice synthesis', progress: { current: 0, total: 100 } },
    { id: 'demo-done', type: 'product_intro_v1', status: 'completed', module: 'Single voice', progress: { current: 100, total: 100 } },
  ];
}

function saveScript(text: string, setSaveMessage: (message: string) => void) {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem('omnix.voice.lastScript', text);
  }
  setSaveMessage('Script saved locally.');
}

function loadScript(setValue: ReturnType<typeof useForm<VoiceFormValues>>['setValue'], setSaveMessage: (message: string) => void) {
  const saved = typeof window !== 'undefined' ? window.localStorage.getItem('omnix.voice.lastScript') : null;
  setValue('text', saved || DEFAULT_SCRIPT);
  setSaveMessage(saved ? 'Loaded saved script.' : 'Loaded example script.');
}

function toggleEffect(effect: string, setEnabledEffects: (value: (current: string[]) => string[]) => void) {
  setEnabledEffects((current) => current.includes(effect) ? current.filter((entry) => entry !== effect) : [...current, effect]);
}

function voiceSynthesisStages(segments: ScriptSegmentRow[]) {
  return [
    { id: 'parse-script', label: 'Detect script characters', resource_class: 'cpu' as const, status: 'queued' as const },
    { id: 'assign-voices', label: 'Apply voice assignments', resource_class: 'cpu' as const, status: 'queued' as const },
    ...segments.map((segment, index) => ({ id: `synthesize-${index.toString().padStart(4, '0')}`, label: `Generate ${segment.speaker} stage ${index + 1}`, resource_class: 'gpu:tts' as const, status: 'queued' as const })),
    { id: 'stitch-audio', label: 'Stitch generated stages', resource_class: 'cpu' as const, status: 'queued' as const },
    { id: 'store-audio', label: 'Save audio output', resource_class: 'cpu' as const, status: 'queued' as const },
  ];
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error ?? new Error('Audio sample could not be read.'));
    reader.readAsDataURL(blob);
  });
}

/** The voice a job names: its asset id, which the TTS provider resolves (WP-4.10). */
function voiceReference(asset: VoiceAsset | undefined): string {
  const value = (asset as { id?: unknown } | undefined)?.id;
  return typeof value === 'string' ? value : '';
}

function voiceFileName(asset: VoiceAsset | undefined): string {
  const value = (asset as { file_name?: unknown } | undefined)?.file_name;
  return typeof value === 'string' ? value : '';
}

function voiceAssetId(asset: VoiceAsset | undefined): string {
  const value = (asset as { id?: unknown } | undefined)?.id;
  return typeof value === 'string' ? value : 'voice';
}

function voiceAssetMetadata(asset: VoiceAsset): Record<string, unknown> {
  const value = (asset as { metadata?: unknown }).metadata;
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function voiceAssetName(asset: VoiceAsset): string {
  const metadata = voiceAssetMetadata(asset);
  const preferred = metadata.profile_name ?? metadata.name ?? metadata.voice_name;
  if (typeof preferred === 'string' && preferred.trim()) return preferred.trim();
  const source = voiceFileName(asset) || voiceAssetId(asset);
  return source.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || voiceAssetId(asset);
}

function voiceProfileName(asset: VoiceAsset): string {
  const metadata = voiceAssetMetadata(asset);
  const preferred = metadata.voice_id ?? metadata.voice_clone_id;
  if (typeof preferred === 'string' && preferred.trim()) return preferred.trim();
  return voiceAssetId(asset).replace(/^voice-cloning:/, '').replace(/^asset:/, '');
}

function voiceInitial(asset: VoiceAsset): string {
  return voiceAssetName(asset).slice(0, 2).toUpperCase() || 'VC';
}

function voiceProfileDescription(asset: VoiceAsset): string {
  return asset.module === 'voice-cloning' ? 'Cloned voice' : 'Local voice';
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatPlaybackTime(value: number): string {
  const safe = Number.isFinite(value) ? Math.max(0, value) : 0;
  const minutes = Math.floor(safe / 60).toString().padStart(2, '0');
  const seconds = Math.floor(safe % 60).toString().padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function estimateDurationFromText(text: string): number {
  return Math.max(1, Math.min(600, text.trim().length / 14));
}

function safeDownloadName(value: string): string {
  return value.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '') || 'voice-output';
}

function settingLabel(name: OutputSettingName): string {
  if (name === 'style') return 'Style Exaggeration';
  return name.replace(/^./, (first) => first.toUpperCase());
}

function settingValueLabel(name: OutputSettingName, value: number): string {
  if (name === 'speed') return `${value.toFixed(2)}x`;
  if (name === 'pitch') return `${value.toFixed(0)} st`;
  if (name === 'volume') return `${value.toFixed(1)} dB`;
  return value.toFixed(2);
}

function rangeMin(name: OutputSettingName): number {
  if (name === 'pitch') return -12;
  if (name === 'volume') return -12;
  if (name === 'speed') return 0.5;
  return 0;
}

function rangeStep(name: OutputSettingName): number {
  if (name === 'pitch') return 1;
  if (name === 'volume') return 0.5;
  return 0.01;
}

/** What each slider changes; the backend maps them per TTS engine (platform/voice/output_settings). */
function settingDescription(name: OutputSettingName): string {
  return {
    stability: 'Higher is steadier and more uniform; lower varies the delivery more (sampling temperature).',
    similarity: 'How closely the clone copies the sample. Below 0.50 Qwen uses only the voice timbre, not the delivery in the sample.',
    style: 'Higher allows more varied, expressive prosody.',
    speed: 'Speaking rate, without changing the pitch.',
    pitch: 'Voice pitch in semitones, without changing the speed.',
    volume: 'Output gain in dB; it never pushes the audio past full scale.',
  }[name];
}

function rangeMax(name: OutputSettingName): number {
  if (name === 'speed') return 2;
  if (name === 'pitch') return 12;
  if (name === 'volume') return 12;
  return 1;
}
