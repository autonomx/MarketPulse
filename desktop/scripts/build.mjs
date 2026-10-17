// Builds the Electron main process, the preload and the shell pages into out/.
import { build } from 'esbuild';
import { cpSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'out');
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const common = { bundle: true, platform: 'node', target: 'node22', format: 'cjs', external: ['electron'], sourcemap: true, logLevel: 'warning' };
await build({ ...common, entryPoints: [join(root, 'src/main/index.ts')], outfile: join(out, 'main.js') });
await build({ ...common, entryPoints: [join(root, 'src/preload/index.ts')], outfile: join(out, 'preload.js') });
await build({ ...common, platform: 'browser', format: 'iife', entryPoints: [join(root, 'src/pages/shell-page.ts')], outfile: join(out, 'pages/shell-page.js') });

cpSync(join(root, 'pages'), join(out, 'pages'), { recursive: true });
mkdirSync(join(out, 'icons'), { recursive: true });
for (const name of ['icon.ico', 'icon.png', 'icon-16.png', 'icon-32.png', 'icon-64.png', 'icon-128.png', 'icon-256.png']) {
  cpSync(join(root, '..', 'brand', 'out', name), join(out, 'icons', name));
}
console.log('built desktop into out/');
