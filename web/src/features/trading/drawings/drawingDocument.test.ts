import { describe, expect, it } from 'vitest';
import { DRAWING_DOCUMENT_SCHEMA_VERSION, drawingDocumentPayload, mergePreserved, upgradeDrawingDocument } from './drawingDocument';
import v1Payload from './fixtures/drawing-document-v1.json';

const instrumentId = 'crypto:BINANCE:spot:BTC-USDT';
const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map((value) => ({ value, color: '', visible: true }));

describe('drawing document upgrade', () => {
  it('upgrades a version 1 payload: properties filled, pixel coordinates dropped', () => {
    const upgraded = upgradeDrawingDocument(v1Payload, instrumentId);
    expect(upgraded).toMatchObject({ schemaVersion: 1, instrumentId, readOnly: false });
    expect(upgraded.drawings.map((drawing) => drawing.toolType)).toEqual(['trend-line', 'fibonacci', 'text']);
    const [trend, fibonacci, text] = upgraded.drawings;
    expect(trend.points).toEqual([
      { time: '2026-10-06T13:00:00.000Z', price: 62114.25 },
      { time: '2026-10-07T09:00:00.000Z', price: 63980.5 },
    ]);
    expect(trend.properties).toEqual({ extendLeft: false, extendRight: false });
    expect(fibonacci.properties).toEqual({ levels: FIB_LEVELS, showLabels: true, labelContent: 'levels', labelSide: 'right', reverse: false, extendLeft: false, extendRight: false });
    expect(fibonacci).toMatchObject({ revision: 1, locked: true, style: { color: '#ffd43b', lineWidth: 1, lineStyle: 'dashed' } });
    expect(text).toMatchObject({ hidden: true, text: 'Breakout retest', properties: {} });
    // The other instrument's drawing is kept verbatim, not dropped.
    expect(upgraded.preserved).toEqual([v1Payload.drawings[3]]);
  });

  it('round-trips the current version unchanged, preserved entries included', () => {
    const upgraded = upgradeDrawingDocument(v1Payload, instrumentId);
    const saved = JSON.parse(JSON.stringify(drawingDocumentPayload(instrumentId, upgraded.drawings, upgraded.preserved)));
    expect(saved.schemaVersion).toBe(DRAWING_DOCUMENT_SCHEMA_VERSION);
    expect(saved.drawings).toContainEqual(v1Payload.drawings[3]);
    expect(upgradeDrawingDocument(saved, instrumentId)).toEqual({ ...upgraded, schemaVersion: 2 });
  });

  it('keeps stored properties of any shape and extra point fields', () => {
    const upgraded = upgradeDrawingDocument({
      schemaVersion: 2,
      drawings: [{
        drawingId: 'a',
        instrumentId,
        toolType: 'fibonacci',
        points: [{ time: '2026-10-07T00:00:00.000Z', price: 1, source: 'replay' }, { time: '2026-10-07T01:00:00.000Z', price: 2 }],
        selected: true,
        revision: 1,
        properties: { levels: [0.5, 1], extendRight: 'yes', label: 'kept' },
      }],
    }, instrumentId);
    expect(upgraded.drawings[0].points[0]).toEqual({ time: '2026-10-07T00:00:00.000Z', price: 1, source: 'replay' });
    expect(upgraded.drawings[0]).toMatchObject({
      selected: false,
      properties: { levels: [0.5, 1], extendRight: 'yes', label: 'kept', extendLeft: false, showLabels: true },
    });
  });

  it('keeps drawings it cannot read, to save them back unchanged', () => {
    const unreadable = [
      { drawingId: 'b', instrumentId, toolType: 'trend-line', points: [{ time: 7, price: 1 }], selected: false, revision: 1 },
      { drawingId: 'c', instrumentId, toolType: 'trend-line', selected: false, revision: 1 },
      'not a drawing',
    ];
    const upgraded = upgradeDrawingDocument({ schemaVersion: 2, instrumentId, drawings: unreadable }, instrumentId);
    expect(upgraded.drawings).toEqual([]);
    expect(upgraded.preserved).toEqual(unreadable);
    expect(drawingDocumentPayload(instrumentId, [], upgraded.preserved).drawings).toEqual(unreadable);
    expect(upgradeDrawingDocument(null, instrumentId)).toEqual({ schemaVersion: 1, instrumentId, drawings: [], preserved: [], readOnly: false });
  });

  it('opens a newer schema read-only', () => {
    const upgraded = upgradeDrawingDocument({
      schemaVersion: 3,
      instrumentId,
      drawings: [{ drawingId: 'a', instrumentId, toolType: 'trend-line', points: [{ time: '2026-10-07T00:00:00.000Z', price: 1 }, { time: '2026-10-07T01:00:00.000Z', price: 2 }], selected: false, revision: 1 }],
    }, instrumentId);
    expect(upgraded.readOnly).toBe(true);
    expect(upgraded.drawings).toHaveLength(1);
  });

  it('opens a document whose schemaVersion is not a number read-only', () => {
    expect(upgradeDrawingDocument({ schemaVersion: '3', instrumentId, drawings: [] }, instrumentId).readOnly).toBe(true);
    expect(upgradeDrawingDocument({ schemaVersion: null, instrumentId, drawings: [] }, instrumentId).readOnly).toBe(true);
    expect(upgradeDrawingDocument({ instrumentId, drawings: [] }, instrumentId).readOnly).toBe(false);
  });

  it('keeps a drawing whose properties are not an object verbatim', () => {
    const odd = {
      drawingId: 'p', instrumentId, toolType: 'trend-line', revision: 1, properties: ['bad'],
      points: [{ time: '2026-10-05T13:30:00.000Z', price: 1 }, { time: '2026-10-05T13:31:00.000Z', price: 2 }],
    };
    const upgraded = upgradeDrawingDocument({ schemaVersion: 2, instrumentId, drawings: [odd] }, instrumentId);
    expect(upgraded.drawings).toEqual([]);
    expect(drawingDocumentPayload(instrumentId, upgraded.drawings, upgraded.preserved).drawings).toEqual([odd]);
  });

  it('merges two loads of preserved entries without duplicates', () => {
    expect(mergePreserved([{ a: 1 }, 'x'], [{ a: 1 }, { b: 2 }])).toEqual([{ a: 1 }, 'x', { b: 2 }]);
  });
});
