import { describe, expect, it } from 'vitest';
import {
  LIVE2D_INSTANCE_DESTROY_OPTIONS,
  isLive2DPack,
  live2dModelUrl,
  live2dMouthShapeForAvatarFrame,
  live2dMouthShapeForViseme,
  prepareLive2DTextures,
  resolveLive2DParameterIndices,
} from './live2dCharacterRenderer';


describe('Live2D character renderer helpers', () => {
  it('releases renderer instances without destroying cached rig textures', () => {
    expect(LIVE2D_INSTANCE_DESTROY_OPTIONS).toMatchObject({
      children: true,
      texture: false,
      textureSource: false,
      baseTexture: false,
    });
  });

  it('maps speech visemes to bounded mouth-open and mouth-form values', () => {
    expect(live2dMouthShapeForViseme('silence')).toEqual({ open: 0, form: 0 });
    expect(live2dMouthShapeForViseme('A').open).toBe(1);
    expect(live2dMouthShapeForViseme('O').form).toBeLessThan(0);
    expect(live2dMouthShapeForViseme('E').form).toBeGreaterThan(0);
    expect(live2dMouthShapeForViseme('MBP').open).toBeLessThan(0.1);
  });

  it('maps PCM envelope frames to Live2D mouth shapes', () => {
    expect(live2dMouthShapeForAvatarFrame('closed')).toEqual({ open: 0, form: 0 });
    expect(live2dMouthShapeForAvatarFrame('small').open).toBeGreaterThan(0);
    expect(live2dMouthShapeForAvatarFrame('medium').open).toBeGreaterThan(
      live2dMouthShapeForAvatarFrame('small').open,
    );
    expect(live2dMouthShapeForAvatarFrame('wide').open).toBeLessThanOrEqual(1);
  });

  it('uses the pinned local model entry path for bundled catalog models', () => {
    const expectedEntryPaths = {
      'open-llm-vtuber-mao-pro': 'runtime/mao_pro.model3.json',
      'open-llm-vtuber-shizuku': 'runtime/shizuku.model3.json',
      'live2d-sample-haru': 'runtime/haru.model3.json',
      'live2d-sample-hiyori-pro': 'runtime/hiyori_pro_t11.model3.json',
      'live2d-sample-epsilon-pro': 'runtime/Epsilon.model3.json',
      'live2d-sample-chitose': 'runtime/chitose.model3.json',
      'live2d-sample-koharu': 'runtime/koharu.model3.json',
      'live2d-sample-haruto': 'runtime/haruto.model3.json',
      'live2d-sample-tororo': 'runtime/tororo.model3.json',
      'live2d-sample-hijiki': 'runtime/hijiki.model3.json',
    };

    Object.entries(expectedEntryPaths).forEach(([modelId, entryPath]) => {
      expect(live2dModelUrl(`character-live2d:${modelId}`)).toBe(
        `/api/character-live2d/assets/${encodeURIComponent(`character-live2d:${modelId}`)}/${entryPath}`,
      );
    });
  });

  it('recognizes only rigged Live2D avatar packs', () => {
    expect(isLive2DPack({ renderer: 'live2d', rig_asset_id: 'character-live2d:model' } as never)).toBe(true);
    expect(isLive2DPack({ renderer: 'live2d', rig_asset_id: null } as never)).toBe(false);
    expect(isLive2DPack({ renderer: 'sprite', rig_asset_id: 'character-live2d:model' } as never)).toBe(false);
  });

  it('rejects Cubism phantom indexes and resolves real model parameters', () => {
    const parameterIds = [
      { getString: () => 'ParamAngleX' },
      { getString: () => 'ParamA' },
    ];
    const coreModel = {
      _parameterIds: parameterIds,
      getParameterCount: () => parameterIds.length,
      getParameterIndex: () => parameterIds.length,
    };

    expect(resolveLive2DParameterIndices(coreModel, ['ParamA'])).toEqual([1]);
    expect(resolveLive2DParameterIndices(coreModel, ['MissingMouthParameter'])).toEqual([]);
  });

  it('supports official Cubism parameter vectors and primes Pixi texture metadata', () => {
    const parameterIds = [
      { getString: () => 'ParamAngleX' },
      { getString: () => 'ParamA' },
    ];
    const coreModel = {
      _parameterIds: {
        getSize: () => parameterIds.length,
        at: (index: number) => parameterIds[index],
      },
      getParameterCount: () => parameterIds.length,
      getParameterIndex: () => { throw new TypeError('CubismId handle required'); },
    };
    const model = { textures: [{ source: {} }, { source: { _gpuData: { 2: 'ready' } } }] };

    expect(resolveLive2DParameterIndices(coreModel, ['ParamA'])).toEqual([1]);
    prepareLive2DTextures(model);
    expect(model.textures[0].source._gpuData).toEqual({});
    expect(model.textures[1].source._gpuData).toEqual({ 2: 'ready' });
  });

  it('reads parameter names from Cubism string handles', () => {
    const parameterIds = [{ getString: () => ({ s: 'PARAM_MOUTH_OPEN_Y' }) }];
    const coreModel = {
      _parameterIds: { getSize: () => 1, at: (index: number) => parameterIds[index] },
      getParameterCount: () => 1,
      getParameterIndex: () => { throw new TypeError('CubismId handle required'); },
    };

    expect(resolveLive2DParameterIndices(coreModel, ['PARAM_MOUTH_OPEN_Y'])).toEqual([0]);
  });
});
