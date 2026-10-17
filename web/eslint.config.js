import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { builtinRules } from 'eslint/use-at-your-own-risk';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const featureRoot = path.join(webRoot, 'src', 'features');
const featureNames = fs.readdirSync(featureRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && entry.name !== 'shared')
  .map((entry) => entry.name)
  .sort();

const fetchAssignments = [
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.computed=false][left.property.name="fetch"]',
    message: 'Do not replace a fetch implementation; use the API transport layer.',
  },
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.computed=true][left.property.value="fetch"]',
    message: 'Do not replace a fetch implementation; use the API transport layer.',
  },
];

const omnixApiClientAssignments = [
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.object.name="omnixApiClient"]',
    message: 'Do not patch omnixApiClient members; extend the typed API client.',
  },
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.object.type="MemberExpression"][left.object.object.name="omnixApiClient"]',
    message: 'Do not patch omnixApiClient members; extend the typed API client.',
  },
];

const omnixGlobalAssignments = [
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.object.name="window"][left.property.name=/^__omnix/]',
    message: 'Do not add state to window.__omnix; use an explicit module contract.',
  },
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.computed=true][left.object.name="window"][left.property.value=/^__omnix/]',
    message: 'Do not add state to window.__omnix; use an explicit module contract.',
  },
];

const mutationObserver = [
  {
    selector: 'NewExpression[callee.name="MutationObserver"]',
    message: 'New MutationObserver usage needs an explicit allow-list entry.',
  },
];

const bodyInsertion = [
  {
    selector: 'CallExpression[callee.object.object.name="document"][callee.object.property.name="body"][callee.property.name=/^(appendChild|insertBefore)$/]',
    message: 'Do not mutate document.body directly; render through React or an approved portal.',
  },
];

const innerHtmlAssignment = [
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.computed=false][left.property.name="innerHTML"]',
    message: 'Use the approved markdown renderer instead of assigning innerHTML.',
  },
  {
    selector: 'AssignmentExpression[left.type="MemberExpression"][left.computed=true][left.property.value="innerHTML"]',
    message: 'Use the approved markdown renderer instead of assigning innerHTML.',
  },
];

const restrictedSyntax = [
  ...fetchAssignments,
  ...omnixApiClientAssignments,
  ...omnixGlobalAssignments,
  ...mutationObserver,
  ...bodyInsertion,
  ...innerHtmlAssignment,
];

function restrictedFeatureImports(currentFeature) {
  const patterns = [];
  for (const otherFeature of featureNames) {
    if (otherFeature === currentFeature) continue;

    const relativeRoots = [
      ...Array.from({ length: 8 }, (_, depth) => '../'.repeat(depth + 1)),
      ...Array.from({ length: 8 }, (_, depth) => `${'../'.repeat(depth + 1)}features/`),
      '@/features/',
    ];

    for (const root of relativeRoots) {
      const featurePath = `${root}${otherFeature}`;
      patterns.push({
        group: [
          `${featurePath}/*`,
          `${featurePath}/**`,
          `!${featurePath}/index`,
          `!${featurePath}/index.ts`,
        ],
        message: `Import ${otherFeature} through its public index.ts API.`,
      });
    }
  }
  return patterns;
}

const mutationObserverAllowList = [
  'src/features/assistant/workspace/assistant-context-controller.ts',
  'src/features/assistant/workspace/chat-message-stream-audio-controller.ts',
  'src/features/assistant/workspace/desktop-companion-controls.ts',
  'src/features/assistant/workspace/desktop-companion-text-surface.ts',
  'src/features/assistant/workspace/live-avatar-presence.ts',
  'src/features/assistant/workspace/live-voice-controller.ts',
  'src/features/assistant/workspace/live-voice-transcript-autoscroll.ts',
  'src/features/assistant/workspace/live-chat-submission-gateway.ts',
  'src/features/assistant/chat/chat-response-metrics-controller.ts',
  'src/features/assistant/chat/chat-sidebar-manager.ts',
  'src/features/assistant/chat/live-chat-workspace.tsx',
  'src/features/assistant/chat/liveCharacterAvatarBridge.ts',
  'src/features/assistant/chat/newChatCoordinator.ts',
  'src/features/assistant/chat/researchProgressController.ts',
  'src/features/assistant/chat/voice-session-evaluation-workspace.tsx',
  'src/features/rpg/RpgWorkspaceHeader.tsx',
  'src/features/storyteller/story-audio-enhancer.ts',
];

const bodyInsertionAllowList = [
  'src/features/rpg/RpgWorldBundleTransfer.tsx',
  'src/features/storyteller/StoryAudioPanel.tsx',
  'src/features/storyteller/StorytellerWorkspace.tsx',
];

function featureImportTarget(importer, specifier) {
  if (typeof specifier !== 'string') return null;
  let target;
  if (specifier.startsWith('@/features/')) {
    target = path.resolve(featureRoot, specifier.slice('@/features/'.length));
  } else if (specifier.startsWith('.')) {
    target = path.resolve(path.dirname(importer), specifier);
  } else {
    return null;
  }

  const relative = path.relative(featureRoot, target);
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    return null;
  }
  const [feature, ...publicPath] = relative.split(path.sep);
  if (!featureNames.includes(feature)) return null;
  return { feature, publicPath };
}

const omnixBoundaryPlugin = {
  rules: {
    // The core rule under a second name, so long functions warn at one
    // length and fail at another (WP-9.5).
    'max-lines-per-function-warn': builtinRules.get('max-lines-per-function'),
    'no-cross-feature-dynamic-import': {
      meta: {
        type: 'problem',
        docs: { description: 'require public APIs for cross-feature dynamic imports' },
        schema: [],
        messages: {
          deepImport: 'Import {{feature}} through its public index.ts API.',
        },
      },
      create(context) {
        const filename = context.filename ?? context.getFilename();
        const importer = path.relative(featureRoot, filename);
        const currentFeature = importer.split(path.sep)[0];
        if (!featureNames.includes(currentFeature)) return {};
        return {
          ImportExpression(node) {
            const specifier = node.source.type === 'Literal' ? node.source.value : null;
            const target = featureImportTarget(filename, specifier);
            if (
              target
              && target.feature !== currentFeature
              && !(target.publicPath.length === 0 || target.publicPath.join('/') === 'index.ts')
            ) {
              context.report({
                node,
                messageId: 'deepImport',
                data: { feature: target.feature },
              });
            }
          },
        };
      },
    },
    'no-core-feature-import': {
      meta: {
        type: 'problem',
        docs: { description: 'keep core web code (api, events, design, shared) independent of features' },
        schema: [],
        messages: {
          featureImport: 'Core web code must not import a feature ({{feature}}); move the code into the feature or let the feature extend a core contract.',
        },
      },
      create(context) {
        const filename = context.filename ?? context.getFilename();
        const check = (node) => {
          const specifier = node.source?.type === 'Literal' ? node.source.value : null;
          const target = featureImportTarget(filename, specifier);
          if (target) {
            context.report({ node, messageId: 'featureImport', data: { feature: target.feature } });
          }
        };
        return {
          ImportDeclaration: check,
          ExportNamedDeclaration: check,
          ExportAllDeclaration: check,
          ImportExpression: check,
        };
      },
    },
    'own-generated-api-types': {
      meta: {
        type: 'problem',
        docs: { description: 'feature code reads gateway types from its own api/generated.ts' },
        schema: [],
        messages: {
          sharedGenerated: "Import gateway types from this feature's api/generated.ts; it re-exports the shared schemas.",
        },
      },
      create(context) {
        const filename = context.filename ?? context.getFilename();
        const sharedGenerated = path.join(webRoot, 'src', 'api', 'generated');
        const check = (node) => {
          const specifier = node.source?.type === 'Literal' ? node.source.value : null;
          if (typeof specifier !== 'string' || !specifier.startsWith('.')) return;
          const target = path.resolve(path.dirname(filename), specifier);
          if (target === sharedGenerated || target.startsWith(`${sharedGenerated}${path.sep}`)) {
            context.report({ node, messageId: 'sharedGenerated' });
          }
        };
        return {
          ImportDeclaration: check,
          ExportNamedDeclaration: check,
          ExportAllDeclaration: check,
          ImportExpression: check,
        };
      },
    },
    'no-app-direct-feature-dynamic-import': {
      meta: {
        type: 'problem',
        docs: { description: 'route application feature loading through ModuleWorkspace or manifests' },
        schema: [],
        messages: {
          directImport: 'Application shell modules must load features through ModuleWorkspace or a manifest.',
        },
      },
      create(context) {
        const filename = context.filename ?? context.getFilename();
        if (!filename.includes(`${path.sep}src${path.sep}app${path.sep}`)) return {};
        if (/[/\\](?:modules|[^/\\]*Manifest)\.(?:[cm]?[jt]sx?)$/i.test(filename)) return {};
        return {
          ImportExpression(node) {
            const specifier = node.source.type === 'Literal' ? node.source.value : null;
            if (typeof specifier !== 'string') return;
            const resolved = specifier.startsWith('@/features/')
              ? path.resolve(featureRoot, specifier.slice('@/features/'.length))
              : specifier.startsWith('.')
                ? path.resolve(path.dirname(filename), specifier)
                : null;
            if (!resolved) return;
            const relative = path.relative(featureRoot, resolved).replaceAll('\\', '/');
            const isWorkspace = relative === 'ModuleWorkspace' || relative === 'ModuleWorkspace.tsx';
            const isFeature = featureNames.some((feature) => (
              relative === feature || relative.startsWith(`${feature}/`)
            ));
            if (isFeature && !isWorkspace) {
              context.report({ node, messageId: 'directImport' });
            }
          },
        };
      },
    },
  },
};

function restrictedSyntaxConfig(files, restrictions) {
  return {
    files,
    rules: {
      'no-restricted-syntax': ['error', ...restrictions],
    },
  };
}

const defaultSyntax = restrictedSyntax;
const withoutMutationObserver = restrictedSyntax.filter(
  (restriction) => !mutationObserver.includes(restriction),
);
const withoutBodyInsertion = restrictedSyntax.filter(
  (restriction) => !bodyInsertion.includes(restriction),
);
const withoutInnerHtml = restrictedSyntax.filter(
  (restriction) => !innerHtmlAssignment.includes(restriction),
);

const appImports = {
  patterns: [
    {
      group: [
        '../features/**',
        '../../features/**',
        '@/features/**',
        '!../features/ModuleWorkspace',
        '!../features/ModuleWorkspace.tsx',
        '!../../features/ModuleWorkspace',
        '!../../features/ModuleWorkspace.tsx',
      ],
      message: 'Application shell modules must load features through ModuleWorkspace or a manifest.',
    },
  ],
};

// Files whose longest function was over 250 lines when the length rule
// arrived (WP-9.5). Each file's limit is that function's length: shrink a
// function, then lower or delete its entry; never raise one.
const functionLengthBaseline = {
  'src/features/assistant/workspace/live-voice-pcm-session.ts': 773,
  'src/features/assistant/chat/CharacterAvatarPanel.tsx': 349,
  'src/features/assistant/chat/CharacterManagementPanel.tsx': 309,
  'src/features/assistant/chat/OmnixRunCardCore.tsx': 464,
  'src/features/image-generation/ImageGenerationWorkspaceMultiModel.tsx': 288,
  'src/features/podcast/PodcastWorkspace.tsx': 303,
  'src/features/rpg/RpgActionComposer.tsx': 551,
  'src/features/rpg/RpgCreateCampaignWizardLegacy.tsx': 443,
  'src/features/rpg/RpgLorePanel.tsx': 373,
  'src/features/rpg/RpgWorkspace.tsx': 713,
  'src/features/rpg/RpgWorldCampaignSetup.tsx': 290,
  'src/features/rpg/RpgWorldEditorShell.tsx': 267,
  'src/features/rpg/RpgWorldEntityEditor.tsx': 309,
  'src/features/rpg/RpgWorldGenerationDashboard.tsx': 388,
  'src/features/rpg/RpgWorldGenerationPanel.tsx': 301,
  'src/features/rpg/RpgWorldProfilePreview.tsx': 280,
  'src/features/rpg/RpgWorldVisualMapPanel.tsx': 299,
  'src/features/trading/TradingAlertsPanel.tsx': 313,
  'src/features/trading/TradingChartAlertOverlay.tsx': 332,
  'src/features/trading/TradingPaperPanel.tsx': 498,
  'src/features/trading/TradingPositionOverlay.tsx': 333,
  'src/features/trading/TradingStrategiesPanel.tsx': 925,
  'src/features/trading/TradingTerminalDock.tsx': 296,
  'src/features/trading/TradingWorkspace.tsx': 607,
  'src/features/trading/indicators/tradingViewBuiltIns.ts': 254,
  'src/features/trading/persistence/useTradingWorkspacePersistence.ts': 263,
  'src/features/voice/VoiceWorkspace.tsx': 499,
};

export default [
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      // Tool caches (Python test runs can leave one here).
      '.pytest_cache/**',
    ],
  },
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{js,jsx,ts,tsx,mjs,cjs,mts,cts}'],
    plugins: {
      'react-hooks': reactHooks,
      omnix: omnixBoundaryPlugin,
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'no-restricted-syntax': ['error', ...defaultSyntax],
    },
  },
  ...mutationObserverAllowList.map((file) => restrictedSyntaxConfig(
    [file],
    withoutMutationObserver,
  )),
  ...bodyInsertionAllowList.map((file) => restrictedSyntaxConfig(
    [file],
    withoutBodyInsertion,
  )),
  restrictedSyntaxConfig(
    ['src/features/assistant/chat/markdownRenderer.ts'],
    withoutInnerHtml,
  ),
  ...featureNames.map((feature) => ({
    files: [`src/features/${feature}/**/*.{js,jsx,ts,tsx}`],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: restrictedFeatureImports(feature),
      }],
    },
  })),
  {
    files: ['src/features/**/*.{js,jsx,ts,tsx}'],
    rules: {
      'omnix/no-cross-feature-dynamic-import': 'error',
    },
  },
  {
    // PA-2.4: only a feature's typed gateway client combines its paths with the kernel's.
    files: ['src/features/**/*.{js,jsx,ts,tsx}'],
    ignores: ['src/features/*/api/gateway.ts', 'src/features/*/api/generated.ts'],
    rules: {
      'omnix/own-generated-api-types': 'error',
    },
  },
  {
    // Function length (WP-9.5): warn above 150 lines, fail above 250.
    // Functions over 250 carry a tracked `baseline WP-9.5` disable.
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['**/*.test.{ts,tsx}', '**/*.spec.{ts,tsx}', 'src/test/**'],
    rules: {
      'omnix/max-lines-per-function-warn': ['warn', { max: 150, skipBlankLines: true, skipComments: true }],
      'max-lines-per-function': ['error', { max: 250, skipBlankLines: true, skipComments: true }],
    },
  },
  ...Object.entries(functionLengthBaseline).map(([file, max]) => ({
    files: [file],
    rules: {
      'max-lines-per-function': ['error', { max, skipBlankLines: true, skipComments: true }],
    },
  })),
  {
    // Core web code never depends on a feature (PA-2.4); tests may combine them.
    files: ['src/{api,events,design,shared}/**/*.{js,jsx,ts,tsx}', 'src/*.{ts,tsx}'],
    ignores: ['**/*.test.{ts,tsx}', '**/*.spec.{ts,tsx}', 'src/main.tsx'],
    rules: {
      'omnix/no-core-feature-import': 'error',
    },
  },
  {
    files: ['src/app/**/*.{js,jsx,ts,tsx}'],
    ignores: ['src/app/modules.ts', 'src/app/*Manifest.ts'],
    rules: {
      'no-restricted-imports': ['error', appImports],
      'omnix/no-app-direct-feature-dynamic-import': 'error',
    },
  },
];
