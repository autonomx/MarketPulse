import { describe, expect, it } from 'vitest';
import { omnixModules } from './modules';
import { moduleRoutePaths, router } from './router';

describe('router', () => {
  it('registers one route for every canonical module', () => {
    expect(moduleRoutePaths).toEqual(omnixModules.map((module) => module.route));
    for (const route of moduleRoutePaths) {
      expect(router.routesByPath[route]).toBeDefined();
    }
  });

  it('registers the root route for default navigation', () => {
    expect(router.routesByPath['/']).toBeDefined();
  });

  it('keeps voice and voice-cloning route registrations distinct', () => {
    expect(router.routesByPath['/voice']).toBeDefined();
    expect(router.routesByPath['/voice-cloning']).toBeDefined();
    expect(omnixModules.find((module) => module.id === 'voice-cloning')?.route).toBe('/voice-cloning');
  });
});
