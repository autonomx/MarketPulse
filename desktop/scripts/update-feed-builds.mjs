// The MP-9.3 update check: builds MarketPulse 0.1.0 and 0.1.1 whose built-in feed is http://127.0.0.1:47890/.
// Serve <out>/0.1.1 there, install 0.1.0, run it with MARKETPULSE_DATA_DIR and MARKETPULSE_UPDATE_CHECKS=1,
// quit, and 0.1.1 is installed. Run: node --experimental-require-module scripts/update-feed-builds.mjs <out>
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(new URL('../package.json', import.meta.url));
const { build, Platform, Arch } = require('electron-builder');
const root = fileURLToPath(new URL('..', import.meta.url));
const out = process.argv[2];
for (const version of ['0.1.0', '0.1.1']) {
  await build({
    projectDir: root,
    targets: Platform.WINDOWS.createTarget(['nsis'], Arch.x64),
    config: {
      extends: `${root}/electron-builder.yml`,
      extraMetadata: { version },
      directories: { output: `${out}/${version}`, buildResources: 'build-resources' },
      publish: [{ provider: 'generic', url: 'http://127.0.0.1:47890/' }],
    },
    publish: 'never',
  });
}
console.log('built both');
