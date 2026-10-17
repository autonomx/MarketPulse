import { describe, expect, it } from 'vitest';
import { rpgMapAssetLabel, rpgMapAssetUrl } from './rpgMapAssets';

describe('rpgMapAssets', () => {
  it('builds an ID-based browser-safe shared asset URL', () => {
    expect(rpgMapAssetUrl('asset:rpg-map:timber-inn-01')).toBe(
      '/api/assets/asset%3Arpg-map%3Atimber-inn-01/file',
    );
  });

  it('rejects paths and data URLs instead of leaking them into the DOM', () => {
    expect(rpgMapAssetUrl('C:\\private\\map.png')).toBeNull();
    expect(rpgMapAssetUrl('../private/map.png')).toBeNull();
    expect(rpgMapAssetUrl('data:image/png;base64,AAAA')).toBeNull();
    expect(rpgMapAssetUrl('')).toBeNull();
  });

  it('provides a readable label for diagnostics', () => {
    expect(rpgMapAssetLabel('asset:rpg-map:frost-haven-base')).toBe('frost haven base');
  });
});
