import { describe, expect, it, vi } from 'vitest';
import { applyConditionDrafts, choiceMatchesDraft, conditionDraft, conditionEditorFields, draftCondition, newConditionDraft, withPriceLineCondition } from './alertConditionDrafts';

const price = (overrides = {}) => ({ condition_type: 'price_above' as string, threshold: '100', parameters: { lookback_bars: 3 }, conditions: undefined as unknown, ...overrides });

describe('multi-condition alert drafts (TVP-1.6)', () => {
  it('turns drafts into conditions and back', () => {
    const moving = { ...newConditionDraft(), source: 'change' as const, lookback: '5', operator: 'moving_up_percent' as const, amount: '2', bars: '3' };
    const condition = draftCondition(moving);
    expect(condition).toEqual({ source: { kind: 'change_percent', lookback_bars: 5 }, operator: 'moving_up_percent', amount: '2', bars: 3 });
    expect(conditionDraft(condition as never)).toMatchObject({ source: 'change', lookback: '5', operator: 'moving_up_percent', amount: '2', bars: '3' });
    expect(draftCondition({ ...newConditionDraft(), operator: 'entering_channel', upper: '1', lower: '2' })).toMatch(/must be above/);
    expect(draftCondition({ ...newConditionDraft(), value: '' })).toMatch(/needs a value/);
    expect(draftCondition({ ...newConditionDraft(), source: 'indicator' })).toMatch(/indicator/);
    // A trendline condition can't be edited in the dialog: the alert keeps it.
    expect(conditionEditorFields({ condition_type: 'conditions', conditions: [{ source: { kind: 'trendline', points: [] }, operator: 'crossing', target: null, amount: null, bars: null }] as never })).toEqual({ conditionDrafts: [], conditionsEditable: false });
  });

  it('makes a legacy alert with extra conditions a conditions alert, its own condition first', () => {
    const input = price();
    expect(applyConditionDrafts(input as never, { condition: 'price_above', conditionDrafts: [{ ...newConditionDraft('50'), source: 'volume', operator: 'greater_than' }] })).toBeNull();
    expect(input).toMatchObject({
      condition_type: 'conditions',
      threshold: '0',
      conditions: [
        { source: { kind: 'price', field: 'close' }, operator: 'crossing_up', target: { kind: 'value', value: '100' } },
        { source: { kind: 'price', field: 'volume' }, operator: 'greater_than', target: { kind: 'value', value: '50' } },
      ],
    });
    const change = price({ condition_type: 'percent_change_below' });
    applyConditionDrafts(change as never, { condition: 'percent_change_below', conditionDrafts: [newConditionDraft('1')] });
    expect((change.conditions as Array<{ source: unknown; operator: string }>)[0]).toMatchObject({ source: { kind: 'change_percent', lookback_bars: 3 }, operator: 'crossing_down' });
    // Without extra conditions a legacy alert is left as it is.
    const untouched = price();
    expect(applyConditionDrafts(untouched as never, { condition: 'price_above', conditionDrafts: [] })).toBeNull();
    expect(untouched.condition_type).toBe('price_above');
  });

  it('refuses more than five conditions, an invalid draft, and extra conditions on a drawing alert', () => {
    const six = Array.from({ length: 5 }, () => newConditionDraft('1'));
    expect(applyConditionDrafts(price() as never, { condition: 'price_above', conditionDrafts: six })).toMatch(/at most 5/);
    expect(applyConditionDrafts(price() as never, { condition: 'price_above', conditionDrafts: [newConditionDraft('')] })).toMatch(/needs a value/);
    expect(applyConditionDrafts(price({ condition_type: 'trendline_crossing' }) as never, { condition: 'trendline_crossing', conditionDrafts: [newConditionDraft('1')] })).toMatch(/one condition/);
  });
});

describe('multi-condition review fixes (TVP-1.6)', () => {
  it('converts a volume alert, and keeps a chart indicator alert\'s condition first', () => {
    const volume = price({ condition_type: 'volume_below', threshold: '500' });
    applyConditionDrafts(volume as never, { condition: 'volume_below', conditionDrafts: [newConditionDraft('1')] });
    expect((volume.conditions as unknown[])[0]).toEqual({ source: { kind: 'price', field: 'volume' }, operator: 'crossing_down', target: { kind: 'value', value: '500' } });
    const indicatorCondition = { source: { kind: 'indicator', indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' }, operator: 'crossing', target: { kind: 'value', value: '70' } };
    const indicator = price({ condition_type: 'conditions', threshold: '0', conditions: [indicatorCondition] });
    expect(applyConditionDrafts(indicator as never, { condition: 'indicator_above', conditionDrafts: [newConditionDraft('100')] })).toBeNull();
    expect((indicator.conditions as unknown[])[0]).toEqual(indicatorCondition);
    expect(indicator.conditions).toHaveLength(2);
  });

  it('reports why conditions can\'t be saved', () => {
    const report = vi.fn();
    expect(applyConditionDrafts(price() as never, { condition: 'price_above', conditionDrafts: [{ ...newConditionDraft(), operator: 'inside_channel', upper: '1', lower: '2' }] }, report)).toMatch(/must be above/);
    expect(report).toHaveBeenCalledWith('The upper value must be above the lower value.');
  });

  it('matches a chart indicator to a stored condition only with the same inputs and line', () => {
    const draft = conditionDraft({ source: { kind: 'indicator', indicator_id: 'rsi', inputs: { period: 14 }, output: 'rsi:14' }, operator: 'greater_than', target: { kind: 'value', value: '70' } } as never)!;
    const rsi = (period: number) => ({ key: 'rsi', label: `RSI ${period}`, inputs: { period }, outputs: [{ key: `rsi:${period}`, title: `RSI ${period}` }] }) as never;
    expect(choiceMatchesDraft(rsi(14), draft)).toBe(true);
    expect(choiceMatchesDraft(rsi(21), draft)).toBe(false);
  });
});

describe('another line as the target (TVP-1.3)', () => {
  const sma = { kind: 'indicator' as const, indicatorId: 'sma', indicatorInputs: { period: 50 }, output: 'sma:50' };
  const smaSource = { kind: 'indicator', indicator_id: 'sma', inputs: { period: 50 }, output: 'sma:50' };

  it('sends a line target and reads it back', () => {
    const draft = { ...newConditionDraft(''), operator: 'crossing_up' as const, targetLine: sma };
    const condition = draftCondition(draft);
    expect(condition).toEqual({ source: { kind: 'price', field: 'close' }, operator: 'crossing_up', target: { kind: 'source', source: smaSource } });
    expect(conditionDraft(condition as never)).toMatchObject({ source: 'close', operator: 'crossing_up', value: '', targetLine: sma });
    const price = draftCondition({ ...newConditionDraft(''), source: 'indicator', indicatorId: 'rsi', output: 'rsi:14', targetLine: { kind: 'price', field: 'hl2' } });
    expect(price).toMatchObject({ target: { kind: 'source', source: { kind: 'price', field: 'hl2' } } });
  });

  it('takes lines as channel bounds, checking the order only between two values', () => {
    const upper = { ...sma, output: 'bb:upper' };
    const lower = { ...sma, output: 'bb:lower' };
    const channel = draftCondition({ ...newConditionDraft(), operator: 'entering_channel', upperLine: upper, lowerLine: lower });
    expect(channel).toEqual({
      source: { kind: 'price', field: 'close' },
      operator: 'entering_channel',
      target: { kind: 'channel', upper: { kind: 'source', source: { ...smaSource, output: 'bb:upper' } }, lower: { kind: 'source', source: { ...smaSource, output: 'bb:lower' } } },
    });
    expect(conditionDraft(channel as never)).toMatchObject({ upperLine: upper, lowerLine: lower });
    expect(draftCondition({ ...newConditionDraft(), operator: 'inside_channel', upperLine: upper, lower: '5' })).toMatchObject({ target: { lower: { kind: 'value', value: '5' } } });
    expect(draftCondition({ ...newConditionDraft(), operator: 'inside_channel', upperLine: upper, lower: '' })).toMatch(/upper and a lower/);
  });

  it('keeps a trendline target uneditable', () => {
    const trendline = { source: { kind: 'price', field: 'close' }, operator: 'crossing', target: { kind: 'source', source: { kind: 'trendline', points: [] } } };
    expect(conditionDraft(trendline as never)).toBeNull();
  });

  it('makes a new price alert against a line a conditions alert', () => {
    const input = price({ condition_type: 'price_below' });
    withPriceLineCondition(input as never, 'price_below', sma);
    expect(input).toMatchObject({
      condition_type: 'conditions', threshold: '0',
      conditions: [{ source: { kind: 'price', field: 'close' }, operator: 'crossing_down', target: { kind: 'source', source: smaSource } }],
    });
    const unchanged = price();
    withPriceLineCondition(unchanged as never, 'price_above', undefined);
    withPriceLineCondition(unchanged as never, 'volume_above', sma);
    expect(unchanged.condition_type).toBe('price_above');
    // Extra conditions follow the line condition.
    const withExtra = price();
    withPriceLineCondition(withExtra as never, 'price_above', sma);
    applyConditionDrafts(withExtra as never, { condition: 'price_above', conditionDrafts: [newConditionDraft('1')] });
    expect((withExtra.conditions as Array<{ operator: string }>).map((item) => item.operator)).toEqual(['crossing_up', 'crossing']);
  });
});
