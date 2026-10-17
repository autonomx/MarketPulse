 
import { useState } from 'react';

import {
  readLatestLiveVoiceCalibration,
  runBrowserLiveVoiceCalibration,
} from '../workspace/live-voice-calibration';
import { useLiveConversationSelector } from '../workspace/live-conversation-store';

export function LiveVoiceCalibrationPanel() {
  const duplex = useLiveConversationSelector((state) => state.duplex);
  const record = duplex.calibration ?? readLatestLiveVoiceCalibration();
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  async function calibrate(): Promise<void> {
    if (running) return;
    setRunning(true);
    setStatus('Measuring room noise…');
    try {
      const next = await runBrowserLiveVoiceCalibration((stage) => {
        if (stage === 'noise') setStatus('Stay quiet while room noise is measured…');
        if (stage === 'echo') setStatus('Playing a short calibration tone…');
        if (stage === 'speech') setStatus('Say “testing one two” in your normal voice…');
        if (stage === 'complete') setStatus('Calibration complete.');
      });
      setStatus(next.resolvedMode === 'echo_aware'
        ? 'Calibration saved. Automatic mode will verify the current device pair when the call connects.'
        : `Automatic mode will stay safe half-duplex: ${humanReason(next.reason)}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Calibration could not be completed.');
    } finally {
      setRunning(false);
    }
  }

  const confidence = Math.round(duplex.confidence * 100);
  return (
    <section className="live-chat-card" aria-labelledby="live-chat-calibration-heading">
      <header>
        <div>
          <p className="eyebrow">Duplex calibration</p>
          <h3 id="live-chat-calibration-heading">Microphone and speakers</h3>
          <p>Automatic mode uses a short local calibration before enabling echo-aware barge-in.</p>
        </div>
        <button type="button" disabled={running} onClick={() => void calibrate()}>
          {running ? 'Calibrating…' : record ? 'Re-run calibration' : 'Calibrate microphone and speakers'}
        </button>
      </header>
      <dl className="live-chat-metrics live-chat-calibration-metrics">
        <div><dt>Resolved mode</dt><dd>{duplex.resolvedMode === 'echo_aware' ? 'Echo-aware' : 'Safe half-duplex'}</dd></div>
        <div><dt>Confidence</dt><dd>{confidence}%</dd></div>
        <div><dt>Status</dt><dd>{record ? humanReason(duplex.reason) : 'Not calibrated'}</dd></div>
        <div><dt>Last calibration</dt><dd>{record ? new Date(record.createdAt).toLocaleString() : 'Never'}</dd></div>
      </dl>
      <p className="live-chat-note">Calibration stores only numeric environment measurements and a device-pair hash. It does not retain conversation audio or transcript text.</p>
      {status ? <p className="live-chat-note" role="status">{status}</p> : null}
    </section>
  );
}

function humanReason(reason: string): string {
  return reason.replaceAll('_', ' ').replace(/^./, (value) => value.toLocaleUpperCase());
}
