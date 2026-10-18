/**
 * Where an update stands, and what the tray, the Help menu and a check the user asked for say about it (roadmap
 * MP-9.3). No Electron here, so the unit tests read it directly.
 */
export type UpdateState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'up-to-date' }
  /** A version the app cannot install itself (an ad hoc signed Mac build): the user downloads it. */
  | { kind: 'available'; version: string }
  | { kind: 'downloading'; version: string; percent: number | null }
  | { kind: 'ready'; version: string }
  | { kind: 'failed' };

export type UpdateAction = 'check' | 'restart' | 'download';

export interface UpdateMenuItem {
  label: string;
  /** What choosing the item does; null when it is a status line. */
  action: UpdateAction | null;
}

/** The update item of the tray and Help menus. */
export function updateMenuItem(state: UpdateState): UpdateMenuItem {
  switch (state.kind) {
    case 'checking':
      return { label: 'Checking for Updates…', action: null };
    case 'downloading':
      return { label: `Downloading MarketPulse ${state.version}…${state.percent === null ? '' : ` ${state.percent}%`}`, action: null };
    case 'ready':
      return { label: `Restart to Update to ${state.version}`, action: 'restart' };
    case 'available':
      return { label: `Download MarketPulse ${state.version}…`, action: 'download' };
    default:
      return { label: 'Check for Updates…', action: 'check' };
  }
}

/** The tray tooltip's update status; empty when there is nothing to say. */
export function updateTooltip(state: UpdateState): string {
  switch (state.kind) {
    case 'downloading':
      return `Downloading MarketPulse ${state.version}…`;
    case 'ready':
      return `MarketPulse ${state.version} is ready: restart to update`;
    case 'available':
      return `MarketPulse ${state.version} is available`;
    default:
      return '';
  }
}

export interface UpdateCheckMessage {
  message: string;
  detail: string;
  /** The first button does `confirm`; the last one closes the message. */
  buttons: string[];
  confirm: UpdateAction | null;
}

/** What a check the user asked for reports once it has an answer; null while it has none. */
export function checkResultMessage(state: UpdateState, currentVersion: string): UpdateCheckMessage | null {
  switch (state.kind) {
    case 'up-to-date':
      return { message: 'MarketPulse is up to date', detail: `Version ${currentVersion} is the latest.`, buttons: ['OK'], confirm: null };
    case 'failed':
      return { message: 'Could not check for updates', detail: 'Check your internet connection and try again.', buttons: ['OK'], confirm: null };
    case 'downloading':
      return {
        message: `Downloading MarketPulse ${state.version}`,
        detail: 'MarketPulse tells you when it is ready to install. Your data is kept.',
        buttons: ['OK'],
        confirm: null,
      };
    case 'ready':
      return {
        message: `MarketPulse ${state.version} is ready to install`,
        detail: 'Restart now to update, or it installs the next time you quit. Your data is kept.',
        buttons: ['Restart Now', 'Later'],
        confirm: 'restart',
      };
    case 'available':
      return {
        message: `MarketPulse ${state.version} is available`,
        detail: 'Download it from the releases page and drag it to Applications. Your data is kept.',
        buttons: ['Open Download Page', 'Later'],
        confirm: 'download',
      };
    default:
      return null;
  }
}
