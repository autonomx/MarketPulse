import { describe, expect, it } from 'vitest';
import { omnixModules } from './modules';

const canonicalModuleIds = [
  'rpg',
  'chatbot',
  'storyteller',
  'audiobook',
  'podcast',
  'voice',
  'voice-cloning',
  'stt',
  'image-generation',
  'trading',
  'providers',
  'models',
  'jobs',
  'assets',
  'reports',
  'settings',
  'diagnostics',
];

describe('omnixModules', () => {
  it('keeps the canonical modules in their order', () => {
    // A module added later (scripts/new_module.py, PA-4.2) joins the list without editing this test.
    const ids = omnixModules.map((module) => module.id);
    expect(ids.filter((id) => (canonicalModuleIds as readonly string[]).includes(id))).toEqual(canonicalModuleIds);
  });

  it('defines a unique route for every module', () => {
    const routes = omnixModules.map((module) => module.route);

    expect(new Set(routes).size).toBe(routes.length);
    expect(routes.length).toBeGreaterThanOrEqual(canonicalModuleIds.length);
    expect(routes.every((route) => route.startsWith('/'))).toBe(true);
  });
});
