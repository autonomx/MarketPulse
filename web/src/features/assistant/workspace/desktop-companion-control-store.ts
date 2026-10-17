export type DesktopCompanionCommand = 'start' | 'pause' | 'resume' | 'mute' | 'unmute' | 'stop';

export type DesktopCompanionControlState = {
  requested: boolean;
  paused: boolean;
  muted: boolean;
  revision: number;
};

type Listener = (state: DesktopCompanionControlState) => void;

class DesktopCompanionControlStore {
  private state: DesktopCompanionControlState = {
    requested: false,
    paused: false,
    muted: true,
    revision: 0,
  };
  private readonly listeners = new Set<Listener>();

  getState(): DesktopCompanionControlState {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  dispatch(command: DesktopCompanionCommand): DesktopCompanionControlState {
    if (command === 'start') this.publish({ requested: true, paused: false });
    else if (command === 'pause') this.publish({ paused: true });
    else if (command === 'resume') this.publish({ requested: true, paused: false });
    else if (command === 'mute') this.publish({ muted: true });
    else if (command === 'unmute') this.publish({ muted: false });
    else this.publish({ requested: false, paused: false });
    return this.state;
  }

  reset(): void {
    this.publish({ requested: false, paused: false, muted: true });
  }

  private publish(patch: Partial<DesktopCompanionControlState>): void {
    this.state = { ...this.state, ...patch, revision: this.state.revision + 1 };
    for (const listener of this.listeners) listener(this.state);
  }
}

export const desktopCompanionControlStore = new DesktopCompanionControlStore();
