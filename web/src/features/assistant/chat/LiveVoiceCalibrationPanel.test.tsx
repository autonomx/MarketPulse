 
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { liveConversationStore } from '../workspace/live-conversation-store';

const calibration = {
  version: 'live-voice-calibration-v1' as const,
  deviceKey: 'device-pair',
  createdAt: 1_000,
  expiresAt: Date.now() + 60_000,
  noiseFloorRms: 0.002,
  playbackRms: 0.04,
  echoGain: 0.2,
  delayMs: 40,
  similarity: 0.9,
  userSpeechSeparation: 2.2,
  confidence: 0.91,
  resolvedMode: 'echo_aware' as const,
  reason: 'calibration_confident',
};

const runCalibration = vi.fn();

vi.mock('../workspace/live-voice-calibration', () => ({
  readLatestLiveVoiceCalibration: () => null,
  runBrowserLiveVoiceCalibration: (...args: unknown[]) => runCalibration(...args),
}));

import { LiveVoiceCalibrationPanel } from './LiveVoiceCalibrationPanel';

describe('LiveVoiceCalibrationPanel', () => {
  beforeEach(() => {
    runCalibration.mockReset();
    liveConversationStore.reset();
  });

  it('shows safe fallback before calibration', () => {
    render(<LiveVoiceCalibrationPanel />);
    expect(screen.getByRole('button', { name: 'Calibrate microphone and speakers' })).toBeInTheDocument();
    expect(screen.getByText('Safe half-duplex')).toBeInTheDocument();
    expect(screen.getByText('Not calibrated')).toBeInTheDocument();
  });

  it('shows the authoritative current-device resolution', () => {
    liveConversationStore.dispatch({
      type: 'duplex',
      duplex: {
        calibration,
        resolvedMode: 'echo_aware',
        reason: 'calibration_confident',
        confidence: 0.91,
      },
    });
    render(<LiveVoiceCalibrationPanel />);

    expect(screen.getByText('Echo-aware')).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByText('Calibration confident')).toBeInTheDocument();
  });

  it('runs calibration and explains that the active pair is verified at call time', async () => {
    runCalibration.mockImplementation(async (onStage?: (stage: string) => void) => {
      onStage?.('noise');
      onStage?.('echo');
      onStage?.('speech');
      onStage?.('complete');
      liveConversationStore.dispatch({
        type: 'duplex',
        duplex: {
          calibration,
          resolvedMode: 'half_duplex',
          reason: 'calibration_device_unverified',
          confidence: 0.91,
        },
      });
      return calibration;
    });
    render(<LiveVoiceCalibrationPanel />);
    fireEvent.click(screen.getByRole('button', { name: 'Calibrate microphone and speakers' }));

    expect(await screen.findByRole('button', { name: 'Re-run calibration' })).toBeInTheDocument();
    expect(screen.getByText('Safe half-duplex')).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('verify the current device pair when the call connects');
  });
});
