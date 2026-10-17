import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

import { defineConfig, type PluginOption } from 'vite';
import react from '@vitejs/plugin-react';
import { gatewayRouting, resolveApiOrigins } from './gateway-routing';
import { resolveListenerHost } from './listener-policy';

function resolveRepositoryRoot(): string {
  try {
    return execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
  } catch {
    return resolve(process.cwd(), '..');
  }
}

const REPOSITORY_ROOT = resolveRepositoryRoot();

const LIVE_VOICE_CRITICAL_PATHS = [
  'src/app/platform/live_voice/speech/speculative_tts.py',
  'src/app/platform/live_voice/speech/tts_lane.py',
  'src/app/platform/live_voice/speech/chat_speculative_tts.py',
  'src/app/platform/chat/live_chat_speculation.py',
  'src/app/platform/chat/live_chat_speculation_inline_stream.py',
  'src/app/platform/live_voice/transport/websocket.py',
  'src/app/platform/live_voice/speech/startup_frame_policy.py',
  'src/app/conversation/tts_stream_contract.py',
  'src/app/providers/faster_qwen3_tts_provider.py',
  'src/app/providers/nemotron_eou_live_websocket.py',
  'src/app/providers/nemotron_eou_quality.py',
  'src/app/providers/nemotron_eou_streaming.py',
  'src/app/providers/vendor/faster_qwen3_tts/model.py',
  'src/app/providers/vendor/faster_qwen3_tts/streaming.py',
  'web/src/features/assistant/workspace/live-speculation-controller.ts',
  'web/src/features/assistant/workspace/live-tts-adaptive-buffer-controller.ts',
  'web/src/features/assistant/workspace/live-voice-controller.ts',
  'web/src/features/assistant/workspace/live-voice-turn-coordinator.ts',
  'web/src/features/assistant/workspace/live-voice-unified-audio-controller.ts',
  'web/src/features/assistant/workspace/live-voice-pcm-session.ts',
  'web/src/features/assistant/workspace/live-voice-pcm-worklet.ts',
  'web/src/features/assistant/workspace/worklets/live-voice-pcm-stream.worklet.ts',
  'web/src/features/assistant/workspace/live-voice-natural-timing.ts',
  'web/src/features/assistant/workspace/live-voice-performance-behavior.ts',
  'web/src/features/assistant/workspace/live-speech-delivery-plan.ts',
  'web/src/features/assistant/workspace/live-speech-synthesis-options.ts',
  'web/src/features/assistant/workspace/live-voice-cue-policy.ts',
] as const;

function reactDevtoolsStandalonePlugin(): PluginOption {
  return {
    name: 'omnix-react-devtools-standalone',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        return html.replace(
          '</head>',
          '    <script src="http://localhost:8097"></script>\n  </head>',
        );
      },
    },
  };
}

function scalableTypographyPostcssPlugin() {
  return {
    postcssPlugin: 'omnix-scalable-typography',
    Declaration(declaration: { prop: string; value: string }) {
      if (declaration.prop.toLowerCase() !== 'font-size' || !declaration.value.includes('px')) return;
      declaration.value = declaration.value.replace(/(-?\d*\.?\d+)px\b/gu, (match, rawValue: string) => {
        const pixels = Number(rawValue);
        if (!Number.isFinite(pixels)) return match;
        const rem = pixels / 16;
        const normalized = Number(rem.toFixed(6));
        return `${normalized}rem`;
      });
    },
  };
}

function resolveGitSha(): string {
  const configured = process.env.VITE_GIT_SHA?.trim();
  if (configured) return configured;
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], {
      cwd: REPOSITORY_ROOT,
      encoding: 'utf8',
    }).trim();
  } catch {
    return 'unknown';
  }
}

function resolveGitDirty(): string {
  const configured = process.env.VITE_GIT_DIRTY?.trim();
  if (configured) return configured;
  try {
    const status = execFileSync(
      'git',
      ['status', '--porcelain', '--untracked-files=normal'],
      { cwd: REPOSITORY_ROOT, encoding: 'utf8' },
    ).trim();
    return status ? 'true' : 'false';
  } catch {
    return 'unknown';
  }
}

function resolveLiveVoiceCriticalDirtyFiles(): string {
  const configured = process.env.VITE_LIVE_VOICE_CRITICAL_DIRTY_FILES?.trim();
  if (configured) return configured;
  try {
    const status = execFileSync(
      'git',
      [
        'status',
        '--porcelain=v1',
        '--untracked-files=normal',
        '--',
        ...LIVE_VOICE_CRITICAL_PATHS,
      ],
      { cwd: REPOSITORY_ROOT, encoding: 'utf8' },
    ).trim();
    if (!status) return '[]';
    const paths = status
      .split(/\r?\n/u)
      .map((line) => line.slice(3).trim())
      .map((path) => {
        const renameIndex = path.lastIndexOf(' -> ');
        return renameIndex >= 0 ? path.slice(renameIndex + 4).trim() : path;
      })
      .filter(Boolean);
    return JSON.stringify([...new Set(paths)]);
  } catch {
    return '[]';
  }
}

export default defineConfig(({ command, mode }) => {
  const hostArgs = process.argv.flatMap((arg, index, args) => (
    arg === '--host' ? [args[index + 1] ?? ''] : arg.startsWith('--host=') ? [arg.slice(7)] : []
  ));
  const listenerHost = resolveListenerHost(process.env, hostArgs.at(-1));
  if (command === 'serve' && !['localhost', '::1'].includes(listenerHost) && !listenerHost.startsWith('127.')) {
    console.warn(`Omnix web listener explicitly exposed beyond loopback: ${listenerHost}`);
  }
  const gitSha = resolveGitSha();
  const gatewayTarget = process.env.VITE_GATEWAY_ORIGIN?.trim()
    || process.env.OMNIX_E2E22_GATEWAY_URL?.trim()
    || 'http://localhost:8000';
  const routing = gatewayRouting(gatewayTarget, resolveApiOrigins(gatewayTarget, REPOSITORY_ROOT));
  process.env.VITE_GIT_SHA ??= gitSha;
  process.env.VITE_GIT_DIRTY ??= resolveGitDirty();
  process.env.VITE_LIVE_VOICE_CRITICAL_DIRTY_FILES ??= resolveLiveVoiceCriticalDirtyFiles();
  process.env.VITE_BUILD_ID ??= `${command}-${mode}-${gitSha.slice(0, 12)}`;
  // The local 5201 service is now Nemotron transcript + Parakeet EOU. Auto
  // authority lets its dedicated EOU candidate end the turn immediately.
  process.env.VITE_ASSISTANT_STT_URL ??= '/api/stt?authority=auto';
  return ({
  plugins: [
    routing.plugin,
    ...(command === 'serve' && mode === 'devtools'
      ? [reactDevtoolsStandalonePlugin()]
      : []),
    react(),
  ],
  css: {
    postcss: {
      plugins: [scalableTypographyPostcssPlugin()],
    },
  },
  build: {
    manifest: true,
    rollupOptions: {
      output: {
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          // The chart engine changes independently of Omnix's optional panels.
          const path = id.replaceAll('\\', '/');
          if (path.includes('/node_modules/lightweight-charts/') || path.includes('/node_modules/fancy-canvas/')) return 'trading-chart-engine';
        },
      },
    },
  },
  server: {
    host: listenerHost,
    port: 5173,
    proxy: routing.proxy,
  },
  preview: {
    host: listenerHost,
    port: 4173,
    proxy: routing.proxy,
  },
  });
});
