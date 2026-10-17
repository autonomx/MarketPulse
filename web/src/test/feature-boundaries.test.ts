import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// WP-9.7: features depend on each other only through public indexes, and the
// feature dependency graph has no cycles.
const featuresRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'features');
const features = readdirSync(featuresRoot).filter((name) => statSync(join(featuresRoot, name)).isDirectory());

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name) ? [path] : [];
  });
}

/** Feature -> the other features its source imports. */
function featureGraph(): Map<string, Set<string>> {
  const graph = new Map(features.map((feature) => [feature, new Set<string>()]));
  for (const feature of features) {
    for (const file of sourceFiles(join(featuresRoot, feature))) {
      const source = readFileSync(file, 'utf8');
      for (const match of source.matchAll(/(?:from\s+|import\s*\(\s*)['"](\.{1,2}\/[^'"]+)['"]/g)) {
        const target = relative(featuresRoot, resolve(dirname(file), match[1]));
        const targetFeature = target.split(sep)[0];
        if (!target.startsWith('..') && targetFeature !== feature && graph.has(targetFeature)) graph.get(feature)?.add(targetFeature);
      }
    }
  }
  return graph;
}

function findCycle(graph: Map<string, Set<string>>): string[] | null {
  const state = new Map<string, 'visiting' | 'done'>();
  const path: string[] = [];
  const visit = (node: string): string[] | null => {
    if (state.get(node) === 'done') return null;
    if (state.get(node) === 'visiting') return [...path.slice(path.indexOf(node)), node];
    state.set(node, 'visiting');
    path.push(node);
    for (const next of graph.get(node) ?? []) {
      const cycle = visit(next);
      if (cycle) return cycle;
    }
    path.pop();
    state.set(node, 'done');
    return null;
  };
  for (const node of graph.keys()) {
    const cycle = visit(node);
    if (cycle) return cycle;
  }
  return null;
}

describe('feature boundaries', () => {
  it('has no cycles between features', () => {
    expect(findCycle(featureGraph())).toBeNull();
  });

  it('keeps settings independent of the storyteller', () => {
    expect(featureGraph().get('settings')?.has('storyteller')).toBe(false);
  });

  it('gives every feature that others import a public index', () => {
    const imported = new Set([...featureGraph().values()].flatMap((targets) => [...targets]));
    for (const feature of imported) {
      expect(() => statSync(join(featuresRoot, feature, 'index.ts')), feature).not.toThrow();
    }
  });
});
