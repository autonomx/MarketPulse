import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CharacterLiveCallRuntime } from './characterClient';
import { installLiveCharacterVisemeBridge } from './liveCharacterVisemeBridge';
import {
  installLiveCharacterAvatarBridge,
  avatarMouthAssetForFrame,
  characterAvatarAssetUrl,
  floatPcmMouthFrame,
  mouthFrameForRms,
  pcmMouthTimeline,
  avatarPresentationState,
  publishCharacterAvatarRuntime,
} from './liveCharacterAvatarBridge';
import { fixture } from '../../../test/fixture';
import { LiveCallVisual } from './LiveCallVisual';

const runtime: CharacterLiveCallRuntime = fixture({
  session_id: 'chat:maya',
  interaction_mode: 'character',
  display_name: 'Maya',
  character_id: 'maya',
  character_profile_version: 1,
  effective_identity_hash: 'identity:maya',
  voice_asset_id: 'voice:maya',
  greeting: 'Hello.',
  avatar_pack: {
    character_id: 'maya',
    version: 1,
    render_mode: 'audio_envelope',
    renderer: 'sprite',
    rig_asset_id: null,
    base_asset_id: 'image:maya-base',
    mouth_frames: { closed: 'image:maya-closed', wide: 'image:maya-wide' },
    blink_frames: {},
    expression_frames: {},
    outfit_frames: {},
    background_asset_ids: {},
    active_outfit: null,
    active_background: null,
    mouth_anchor: {},
    created_at: '2026-07-10T00:00:00Z',
    updated_at: '2026-07-10T00:00:00Z',
  },
  speech_style: {
    speed: 1,
    temperature: 0.7,
    top_k: 20,
    top_p: 0.8,
    repetition_penalty: 1.05,
    expressiveness: 'conversational',
    emotion: 'neutral',
    interruption_style: 'natural',
  },
  read_memory: true,
  write_memory: true,
  shared_memory_access: 'none',
  memory_snapshot_id: null,
  preload: {
    profile_loaded: true,
    voice_resolved: true,
    avatar_pack_loaded: true,
    memory_snapshot_loaded: true,
    memory_record_count: 0,
    preload_ms: 1,
    resolved_at: '2026-07-10T00:00:00Z',
  },
});

const visemeRuntime: CharacterLiveCallRuntime = {
  ...runtime,
  avatar_pack: {
    ...runtime.avatar_pack!,
    render_mode: 'viseme',
    mouth_frames: {
      closed: 'image:maya-closed',
      silence: 'image:maya-closed',
      A: 'image:maya-A',
      E: 'image:maya-E',
      U: 'image:maya-U',
    },
  },
};

const live2dRuntime: CharacterLiveCallRuntime = {
  ...runtime,
  avatar_pack: {
    ...runtime.avatar_pack!,
    renderer: 'live2d',
    rig_asset_id: 'character-live2d:test-maya',
    base_asset_id: null,
    mouth_frames: {},
  },
};

beforeEach(() => {
  // The bridges act only while Chat is the active workspace.
  window.history.replaceState(null, '', '/chatbot');
  installLiveCharacterAvatarBridge();
  installLiveCharacterVisemeBridge();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  publishCharacterAvatarRuntime(null);
});

function renderVisual(voiceMode = 'idle', thinking = false) {
  return render(<LiveCallVisual voiceMode={voiceMode} thinking={thinking} />);
}

function speakPcm(): void {
  const samples = new Int16Array(2_400);
  samples.fill(16_000);
  act(() => {
    window.dispatchEvent(new CustomEvent('omnix:character-avatar-pcm', { detail: { samples, sampleRate: 24_000 } }));
    vi.advanceTimersByTime(30);
  });
}

describe('live character avatar audio envelope', () => {
  it('maps RMS levels to four stable mouth states', () => {
    expect(mouthFrameForRms(0)).toBe('closed');
    expect(mouthFrameForRms(0.02)).toBe('small');
    expect(mouthFrameForRms(0.05)).toBe('medium');
    expect(mouthFrameForRms(0.2)).toBe('wide');
    expect(floatPcmMouthFrame(new Float32Array(32).fill(0.1))).toBe('wide');
  });

  it('produces a compact timeline and browser-safe asset URL', () => {
    const samples = new Int16Array(4800);
    samples.fill(0, 0, 1200);
    samples.fill(4000, 1200, 2400);
    samples.fill(14000, 2400, 3600);
    samples.fill(0, 3600);
    const timeline = pcmMouthTimeline(samples, 24_000, 50);
    expect(timeline[0]).toEqual({ offsetMs: 0, frame: 'closed' });
    expect(timeline.some((point) => point.frame === 'wide')).toBe(true);
    expect(characterAvatarAssetUrl('image:maya closed')).toBe('/api/assets/image%3Amaya%20closed/file');
  });

  it('maps audio-envelope states onto generated viseme assets', () => {
    expect(avatarMouthAssetForFrame(visemeRuntime.avatar_pack!, 'wide')).toBe('image:maya-A');
    expect(avatarMouthAssetForFrame(visemeRuntime.avatar_pack!, 'medium')).toBe('image:maya-E');
    expect(avatarMouthAssetForFrame(visemeRuntime.avatar_pack!, 'small')).toBe('image:maya-U');
    expect(avatarMouthAssetForFrame(visemeRuntime.avatar_pack!, 'closed')).toBe('image:maya-closed');
  });

  it('derives listening, thinking, speaking, and error presentation states', () => {
    expect(avatarPresentationState('closed', 'listening', false)).toBe('listening');
    expect(avatarPresentationState('closed', 'listening', true)).toBe('thinking');
    expect(avatarPresentationState('closed', 'speaking', true)).toBe('speaking');
    expect(avatarPresentationState('wide', 'idle', false)).toBe('speaking');
    expect(avatarPresentationState('closed', 'error', false)).toBe('error');
    expect(avatarPresentationState('closed', 'idle', false)).toBe('idle');
  });

  it('shows the avatar in place of the orb while a character runtime is published', () => {
    const { container } = renderVisual();
    const stage = container.querySelector<HTMLElement>('.assistant-live-visual-stage');
    expect(stage?.querySelector('.assistant-voice-orb')).not.toBeNull();

    act(() => publishCharacterAvatarRuntime(runtime));

    expect(stage?.getAttribute('aria-label')).toBe('Live character visual');
    expect(stage?.querySelector('.assistant-live-character-avatar')).not.toBeNull();
    expect(stage?.querySelector('.assistant-voice-orb')).toBeNull();
    expect(stage?.dataset.hasCharacterAvatar).toBe('true');

    act(() => publishCharacterAvatarRuntime(null));

    expect(stage?.querySelector('.assistant-live-character-avatar')).toBeNull();
    expect(stage?.querySelector('.assistant-voice-orb')).not.toBeNull();
    expect(stage?.dataset.hasCharacterAvatar).toBeUndefined();
  });

  it('animates mouth frames from live PCM even when the call is idle', () => {
    vi.useFakeTimers();
    renderVisual();
    act(() => publishCharacterAvatarRuntime(runtime));
    speakPcm();

    const avatar = document.querySelector<HTMLElement>('.assistant-live-character-avatar');
    const image = avatar?.querySelector<HTMLImageElement>('img');
    const caption = avatar?.querySelector<HTMLElement>('figcaption');
    expect(avatar?.dataset.mouthFrame).toBe('wide');
    expect(avatar?.dataset.voiceMode).toBe('speaking');
    expect(image?.getAttribute('src')).toBe('/api/assets/image%3Amaya-wide/file');
    expect(caption?.textContent).toBe('Maya is speaking');
  });

  it('keeps precise viseme packs isolated from PCM envelope updates', () => {
    vi.useFakeTimers();
    renderVisual();
    act(() => publishCharacterAvatarRuntime(visemeRuntime));
    speakPcm();

    const avatar = document.querySelector<HTMLElement>('.assistant-live-character-avatar');
    const image = avatar?.querySelector<HTMLImageElement>('img');
    expect(avatar?.dataset.mouthFrame).toBe('closed');
    expect(image?.getAttribute('src')).toBe('/api/assets/image%3Amaya-closed/file');
  });

  it('uses worklet playback frames for Live2D and ignores arrival-time PCM', () => {
    vi.useFakeTimers();
    renderVisual();
    act(() => publishCharacterAvatarRuntime(live2dRuntime));

    const observed: string[] = [];
    const onFrame = (event: Event): void => {
      const frame = (event as CustomEvent<{ frame?: string }>).detail?.frame;
      if (frame) observed.push(frame);
    };
    window.addEventListener('omnix:character-avatar-frame', onFrame);
    try {
      const samples = new Int16Array(2_400);
      samples.fill(16_000);
      window.dispatchEvent(new CustomEvent('omnix:character-avatar-pcm', {
        detail: { samples, sampleRate: 24_000 },
      }));
      vi.advanceTimersByTime(150);
      expect(observed).toEqual([]);

      window.dispatchEvent(new CustomEvent('omnix:live-call-diagnostic', {
        detail: {
          source: 'audio_worklet',
          event: 'worklet_avatar_frame',
          details: { frame: 'wide', rms: 0.2 },
        },
      }));
      expect(observed).toEqual(['wide']);

      window.dispatchEvent(new CustomEvent('omnix:live-call-diagnostic', {
        detail: {
          source: 'audio_worklet',
          event: 'worklet_underrun',
          details: {},
        },
      }));
      expect(observed).toEqual(['wide', 'closed']);
    } finally {
      window.removeEventListener('omnix:character-avatar-frame', onFrame);
    }
  });
});
