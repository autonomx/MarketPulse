export type LivePanelMode = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';

export type LivePanelState = {
  mode: LivePanelMode;
  transcriptVisible: boolean;
  controlsVisible: boolean;
};

export function createLivePanelState(input: Partial<LivePanelState> = {}): LivePanelState {
  return {
    mode: input.mode ?? 'idle',
    transcriptVisible: input.transcriptVisible ?? true,
    controlsVisible: input.controlsVisible ?? true,
  };
}

export function setLivePanelMode(state: LivePanelState, mode: LivePanelMode): LivePanelState {
  return { ...state, mode };
}

export function canInterruptLivePanel(state: LivePanelState): boolean {
  return state.mode === 'thinking' || state.mode === 'speaking';
}
