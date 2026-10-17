// Builds the Windows installer from out/ and the staged runtime (roadmap Phase 9).
import { build } from 'electron-builder';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const stage = join(root, '..', 'build', 'stage');
for (const part of ['python/python.exe', 'postgres/bin/postgres.exe', 'app/src/marketpulse/supervise.py', 'web/dist/index.html']) {
  if (!existsSync(join(stage, part))) {
    console.error(`build/stage/${part} is missing: run \`python build/stage.py all\` (after \`npm --prefix web run build\`)`);
    process.exit(1);
  }
}

await build({
  projectDir: root,
  config: join(root, 'electron-builder.yml'),
  publish: process.argv.includes('--publish') ? 'always' : 'never',
});
console.log('installer written to desktop/release/');
