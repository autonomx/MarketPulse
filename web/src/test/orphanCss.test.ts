import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// A stylesheet only applies when a module imports it: the scanner and replay
// panels shipped unstyled because theirs never were.
const srcRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');

// Unused since before the web/ move; their owner decides whether to wire or delete them.
const KNOWN_UNIMPORTED = new Set(['features/rpg/RpgWorldDesignFixtures.css', 'features/rpg/RpgWorldsCampaignsLibrary.css']);

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(ts|tsx|css)$/.test(name) ? [path] : [];
  });
}

describe('stylesheets', () => {
  it('are each imported by a module or another stylesheet', () => {
    const all = files(srcRoot);
    const imported = new Set<string>();
    for (const file of all) {
      for (const match of readFileSync(file, 'utf8').matchAll(/(?:import\s+|@import\s+|from\s+)['"]([^'"]+\.css)['"]/g)) {
        imported.add(resolve(dirname(file), match[1]));
      }
    }
    const unimported = all
      .filter((file) => file.endsWith('.css') && !imported.has(file))
      .map((file) => relative(srcRoot, file).split(sep).join('/'))
      .filter((path) => !KNOWN_UNIMPORTED.has(path));
    expect(unimported).toEqual([]);
  });
});
