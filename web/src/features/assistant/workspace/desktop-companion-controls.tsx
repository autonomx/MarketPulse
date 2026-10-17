import { useEffect, useState, useSyncExternalStore } from 'react';
import { desktopCompanionControlStore } from './desktop-companion-control-store';
import { DESKTOP_COMPANION_STATUS_EVENT } from '../../../events/bus';

type StatusDetail = {
  phase?: string;
  reason?: string;
  reaction?: string | null;
};

const subscribe = (listener: () => void) => desktopCompanionControlStore.subscribe(listener);
const getState = () => desktopCompanionControlStore.getState();

/** Start, pause, mute and stop Companion Watch, with the watch controller's latest status. */
export function DesktopCompanionControls() {
  const state = useSyncExternalStore(subscribe, getState, getState);
  const [status, setStatus] = useState<StatusDetail>({ phase: 'off', reason: 'not_started' });

  useEffect(() => {
    const handleStatus = (event: Event) => setStatus((event as CustomEvent<StatusDetail>).detail ?? {});
    window.addEventListener(DESKTOP_COMPANION_STATUS_EVENT, handleStatus);
    return () => window.removeEventListener(DESKTOP_COMPANION_STATUS_EVENT, handleStatus);
  }, []);

  return (
    <div className="desktop-companion-controls" data-omnix-desktop-companion-controls="true">
      <span className="desktop-companion-controls__title">Companion Watch</span>
      <strong className="desktop-companion-controls__status" title={status.reason ?? ''}>{statusLabel(status.phase, status.reason)}</strong>
      <div className="desktop-companion-controls__actions">
        <button type="button" className="desktop-companion-control desktop-companion-start" disabled={state.requested} onClick={() => desktopCompanionControlStore.dispatch('start')}>
          {state.requested ? 'Watching' : 'Start'}
        </button>
        <button type="button" className="desktop-companion-control desktop-companion-pause" disabled={!state.requested} onClick={() => desktopCompanionControlStore.dispatch(state.paused ? 'resume' : 'pause')}>
          {state.paused ? 'Resume' : 'Pause'}
        </button>
        <button type="button" className="desktop-companion-control desktop-companion-mute" disabled={!state.requested} aria-pressed={state.muted} onClick={() => desktopCompanionControlStore.dispatch(state.muted ? 'unmute' : 'mute')}>
          {state.muted ? 'Muted' : 'Sound on'}
        </button>
        <button type="button" className="desktop-companion-control desktop-companion-stop" disabled={!state.requested} onClick={() => desktopCompanionControlStore.dispatch('stop')}>
          Stop
        </button>
      </div>
    </div>
  );
}

export function statusLabel(phase = 'off', reason = ''): string {
  if (phase === 'analyzing') return 'Analyzing';
  if (phase === 'observation_ready') return 'Observed';
  if (phase === 'watching_idle') return 'Watching';
  if (phase === 'paused') return 'Paused';
  if (phase === 'backing_off') return 'Backoff';
  if (phase === 'error') return reason.includes('remote_vision_not_allowed') ? 'Remote blocked' : 'Error';
  if (reason === 'preflight_running') return 'Testing model';
  return 'Off';
}
