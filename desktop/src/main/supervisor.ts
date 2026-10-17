import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { createWriteStream, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import type { DesktopPaths } from './paths';
import type { DesktopSettings } from './settings';

export type SupervisorEvent =
  | { event: 'step'; step: string; message: string }
  | { event: 'ready'; origin: string; first_run: boolean }
  | { event: 'error'; step: string; message: string; log?: string | null }
  | { event: 'result'; id: number; cmd: string; [key: string]: unknown }
  | { event: 'stopped' };

// Only what Windows Python needs is passed on; marketpulse.env builds the rest (roadmap MP-3.1).
const SYSTEM_VARIABLES = [
  'SystemRoot', 'SYSTEMROOT', 'windir', 'SystemDrive', 'TEMP', 'TMP', 'USERPROFILE', 'USERNAME', 'LOCALAPPDATA',
  'APPDATA', 'PROGRAMDATA', 'COMPUTERNAME', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS', 'PATH', 'Path',
  'PATHEXT', 'HOME', 'LANG', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy',
];

/**
 * The backend supervisor (marketpulse.supervise) as seen from the shell: one child process, JSON events on its
 * stdout, JSON commands on its stdin. Closing stdin (quit, or this process dying) stops the whole backend.
 */
export class BackendSupervisor extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, (result: Record<string, unknown>) => void>();
  ready: { origin: string; first_run: boolean } | null = null;

  constructor(private readonly paths: DesktopPaths, private readonly settings: DesktopSettings,
              private readonly secrets: Record<string, string>, private readonly googleClientId: string | null = null) {
    super();
  }

  launchConfigPath(): string {
    return join(this.paths.data, 'launch.json');
  }

  writeLaunchConfig(): void {
    mkdirSync(this.paths.data, { recursive: true });
    writeFileSync(this.launchConfigPath(), JSON.stringify({
      install_root: this.paths.resources,
      data_dir: this.paths.data,
      http_port: this.settings.httpPort,
      pg_port: this.settings.pgPort,
      metrics_port: this.settings.metricsPort,
      notify_port: this.settings.notifyPort,
      postgres_bin: this.paths.postgresBin,
      // Google sign-in when the build carries a Desktop OAuth client (roadmap §4.7); its secret comes in the env.
      ...(this.googleClientId ? { google_client_id: this.googleClientId } : {}),
    }, null, 2), 'utf8');
  }

  start(): void {
    if (this.child) return;
    this.ready = null;
    this.writeLaunchConfig();
    mkdirSync(this.paths.logs, { recursive: true });
    const env: Record<string, string> = {};
    for (const name of SYSTEM_VARIABLES) {
      const value = process.env[name];
      if (value) env[name] = value;
    }
    Object.assign(env, this.secrets);
    const child = spawn(this.paths.python, ['-m', 'marketpulse.supervise', '--config', this.launchConfigPath()], {
      cwd: this.paths.data,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    this.child = child;
    const stderr = createWriteStream(join(this.paths.logs, 'supervisor.log'), { flags: 'a' });
    child.stderr.pipe(stderr);
    createInterface({ input: child.stdout }).on('line', (line) => this.receive(line));
    child.on('exit', (code) => {
      this.child = null;
      stderr.end();
      this.emit('exit', code);
    });
    child.on('error', (error) => this.emit('event', { event: 'error', step: 'launch', message: error.message } satisfies SupervisorEvent));
  }

  private receive(line: string): void {
    let event: SupervisorEvent;
    try {
      event = JSON.parse(line) as SupervisorEvent;
    } catch {
      return;
    }
    if (event.event === 'ready') this.ready = { origin: event.origin, first_run: event.first_run };
    if (event.event === 'result') {
      const resolveResult = this.pending.get(event.id);
      this.pending.delete(event.id);
      resolveResult?.(event);
    }
    this.emit('event', event);
  }

  /** Sends a command and waits for its result. */
  command(cmd: string, timeoutMs = 60_000): Promise<Record<string, unknown>> {
    const child = this.child;
    if (!child) return Promise.reject(new Error('the backend is not running'));
    const id = this.nextId++;
    return new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${cmd} timed out`));
      }, timeoutMs);
      this.pending.set(id, (result) => {
        clearTimeout(timer);
        resolvePromise(result);
      });
      child.stdin.write(`${JSON.stringify({ cmd, id })}\n`);
    });
  }

  /** Closes stdin, which stops the gateway and PostgreSQL; waits up to `timeoutMs` before killing. */
  stop(timeoutMs = 45_000): Promise<void> {
    const child = this.child;
    if (!child) return Promise.resolve();
    return new Promise((resolvePromise) => {
      const timer = setTimeout(() => child.kill(), timeoutMs);
      child.once('exit', () => {
        clearTimeout(timer);
        resolvePromise();
      });
      child.stdin.end();
    });
  }
}
