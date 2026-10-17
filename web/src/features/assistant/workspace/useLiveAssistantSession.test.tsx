import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { createAudioCaptureState } from './audio-capture';
import type { BrowserAudioCaptureSession } from './audio-capture-browser';
import type { LiveAssistantTurnResult } from './live-orchestrator';
import { useLiveAssistantSession, type LiveAssistantSessionController } from './useLiveAssistantSession';

function captureSession(): BrowserAudioCaptureSession {
  return {
    state: createAudioCaptureState({
      active: true,
      permission: 'granted',
      devices: [{ id: 'mic-1', label: 'Microphone' }],
      selectedDeviceId: 'mic-1',
    }),
    stream: { getTracks: () => [] },
  };
}

function liveTurnResult(): LiveAssistantTurnResult {
  return {
    sessionId: 'session:voice',
    transcript: { text: 'hello' },
    modelRequest: { provider: 'local', model: 'qwen', messages: [] },
    modelResponse: { content: [{ kind: 'text', text: 'hi' }] },
    assistantText: 'hi',
    synthesis: { audioUrl: 'blob:hi' },
    playbackItem: { id: 'playback:1', text: 'hi', createdAt: '2026-06-23T09:00:01Z' },
    stages: ['transcribed', 'responded', 'synthesized', 'queued'],
    events: [],
  };
}

describe('useLiveAssistantSession', () => {
  it('starts capture, runs a captured turn, and stops capture', async () => {
    const stopCapture = vi.fn();
    const controller: LiveAssistantSessionController = {
      startCapture: vi.fn(async () => captureSession()),
      stopCapture,
      readCapturedAudio: vi.fn(async () => new ArrayBuffer(2)),
      runTurn: vi.fn(async () => liveTurnResult()),
    };

    const { result } = renderHook(() => useLiveAssistantSession(controller));

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.status).toBe('capturing');

    await act(async () => {
      await result.current.submitCapturedTurn();
    });

    expect(controller.readCapturedAudio).toHaveBeenCalledOnce();
    expect(controller.runTurn).toHaveBeenCalledOnce();
    expect(stopCapture).toHaveBeenCalledOnce();
    expect(result.current.status).toBe('ready');
    expect(result.current.result?.assistantText).toBe('hi');
  });

  it('reports a clear error when submitting without capture', async () => {
    const controller: LiveAssistantSessionController = {
      startCapture: vi.fn(async () => captureSession()),
      stopCapture: vi.fn(),
      readCapturedAudio: vi.fn(async () => new ArrayBuffer(2)),
      runTurn: vi.fn(),
    };

    const { result } = renderHook(() => useLiveAssistantSession(controller));

    await act(async () => {
      await result.current.submitCapturedTurn();
    });

    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('No active capture session.');
  });
});
