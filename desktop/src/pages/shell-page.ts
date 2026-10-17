// The splash and error pages' script: they show what the shell reports through the preload's `marketPulseShell`.
interface ShellStatus {
  kind: 'step' | 'error';
  message: string;
  log?: string | null;
  step?: string;
}

interface ShellBridge {
  onStatus(listener: (status: ShellStatus) => void): void;
  retry(): void;
  newPorts(): void;
  restore(): void;
  exportDiagnostics(): void;
  revealLogs(): void;
  quit(): void;
}

const bridge = (window as unknown as { marketPulseShell?: ShellBridge }).marketPulseShell;

function text(id: string, value: string): void {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

bridge?.onStatus((status) => {
  if (status.kind === 'step') text('status', status.message);
  else {
    text('message', status.message);
    text('log', status.log ?? '');
    const newPort = document.getElementById('new-port');
    if (newPort) newPort.hidden = !status.message.startsWith('port_busy');
    const restore = document.getElementById('restore');
    if (restore) restore.hidden = status.step !== 'migrate';
  }
});

document.getElementById('retry')?.addEventListener('click', () => bridge?.retry());
document.getElementById('new-port')?.addEventListener('click', () => bridge?.newPorts());
document.getElementById('logs')?.addEventListener('click', () => bridge?.revealLogs());
document.getElementById('restore')?.addEventListener('click', () => bridge?.restore());
document.getElementById('diagnostics')?.addEventListener('click', () => bridge?.exportDiagnostics());
document.getElementById('quit')?.addEventListener('click', () => bridge?.quit());
