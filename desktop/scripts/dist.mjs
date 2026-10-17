// Builds the installer for the staged runtime's platform from out/ (roadmap Phases 9 and 11):
// Windows NSIS from a windows-x64 stage, macOS dmg + zip from a macos-arm64 or macos-x64 stage (on a Mac).
import { Arch, build, Platform } from 'electron-builder';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const stage = join(root, '..', 'build', 'stage');
const fail = (message) => {
  console.error(message);
  process.exit(1);
};

const infoPath = join(stage, 'build-info.json');
if (!existsSync(infoPath)) fail('build/stage/build-info.json is missing: run `python build/stage.py all`');
const info = JSON.parse(readFileSync(infoPath, 'utf8'));
const stagedPlatform = info.platform ?? 'windows-x64';
const mac = stagedPlatform.startsWith('macos-');
if (mac !== (process.platform === 'darwin')) {
  fail(`build/stage holds the ${stagedPlatform} runtime; build its installer on ${mac ? 'macOS' : 'Windows'}`);
}

const required = mac
  ? ['python/bin/python3', 'postgres/bin/postgres']
  : ['python/python.exe', 'postgres/bin/postgres.exe'];
for (const part of [...required, 'app/src/marketpulse/supervise.py', 'web/dist/index.html']) {
  if (!existsSync(join(stage, part))) {
    fail(`build/stage/${part} is missing: run \`python build/stage.py all\` (after \`npm --prefix web run build\`)`);
  }
}

// Notarize only with Apple credentials (an App Store Connect API key, or an Apple ID app-specific password).
const appleCredentials = Boolean(
  (process.env.APPLE_API_KEY && process.env.APPLE_API_KEY_ID && process.env.APPLE_API_ISSUER)
  || (process.env.APPLE_ID && process.env.APPLE_APP_SPECIFIC_PASSWORD && process.env.APPLE_TEAM_ID),
);
if (mac && !appleCredentials) console.warn('no Apple notarization credentials: the app is not notarized');

const targets = mac
  ? Platform.MAC.createTarget(['dmg', 'zip'], stagedPlatform === 'macos-arm64' ? Arch.arm64 : Arch.x64)
  : Platform.WINDOWS.createTarget(['nsis'], Arch.x64);

await build({
  projectDir: root,
  targets,
  config: {
    extends: join(root, 'electron-builder.yml'),
    ...(mac ? { mac: { notarize: appleCredentials } } : {}),
  },
  publish: process.argv.includes('--publish') ? 'always' : 'never',
});
console.log(`${stagedPlatform} installer written to desktop/release/`);
