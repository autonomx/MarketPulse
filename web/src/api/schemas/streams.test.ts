import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import contract from '../generated/openapi.json';
import {
  chatStreamEventSchema,
  jobEventSchema,
  jobOutputRefs,
  parseJson,
  parseSseData,
  tradingStreamMessageSchema,
  ttsControlEventSchema,
} from './streams';

type SchemaComponent = { properties?: Record<string, unknown> };
const components = contract.components.schemas as Record<string, SchemaComponent>;

function componentFields(name: string): string[] {
  return Object.keys(components[name]?.properties ?? {});
}

function objectFields(schema: z.ZodType): string[] {
  return Object.keys((schema as z.ZodObject).shape);
}

const bar = {
  binding_id: 'binding-1',
  instrument_id: 'BTC-USD',
  interval: '1m',
  start_time: '2026-10-03T10:00:00+00:00',
  end_time: '2026-10-03T10:01:00+00:00',
  open: '100.5',
  high: '101',
  low: '99.75',
  close: '100',
  volume: '12.5',
  is_final: false,
  provider_event_id: null,
  provider_sequence: null,
  ingestion_revision: 1,
};

describe('stream boundary schemas', () => {
  it('parses JSON and rejects bad JSON or the wrong shape', () => {
    expect(parseJson(ttsControlEventSchema, '{"type":"ready","sample_rate":24000}')).toEqual({ type: 'ready', sample_rate: 24000 });
    expect(parseJson(ttsControlEventSchema, '{not-json')).toBeNull();
    expect(parseJson(ttsControlEventSchema, '{"sample_rate":"fast"}')).toBeNull();
  });

  it('reads the data line of an SSE block', () => {
    const block = 'event: message\ndata: {"type":"text_chunk","text":"Hi"}\n';

    expect(parseSseData(chatStreamEventSchema, block)).toEqual({ type: 'text_chunk', text: 'Hi' });
    expect(parseSseData(chatStreamEventSchema, 'event: ping\n')).toBeNull();
    expect(parseSseData(chatStreamEventSchema, 'data: {"text":"no type"}')).toBeNull();
  });

  it('keeps fields the gateway adds', () => {
    expect(parseJson(chatStreamEventSchema, '{"type":"done","usage":{"tokens":3}}')).toEqual({ type: 'done', usage: { tokens: 3 } });
  });

  it('accepts the trading bar the stream sends and refuses unknown message types', () => {
    expect(parseJson(tradingStreamMessageSchema, JSON.stringify({ type: 'bar', bar }))).toEqual({ type: 'bar', bar });
    expect(parseJson(tradingStreamMessageSchema, '{"type":"error","code":"x","message":"y"}')).toEqual({ type: 'error', code: 'x', message: 'y' });
    expect(parseJson(tradingStreamMessageSchema, JSON.stringify({ type: 'bar', bar: { ...bar, close: 100 } }))).toBeNull();
    expect(parseJson(tradingStreamMessageSchema, '{"type":"quote"}')).toBeNull();
  });

  it('requires a job id on job events', () => {
    expect(jobEventSchema.safeParse({ id: 1, job_id: 'job-1', event_type: 'job.updated', payload: {} }).success).toBe(true);
    expect(jobEventSchema.safeParse({ id: 'job-1' }).success).toBe(false);
  });

  it('keeps only well-formed job output references', () => {
    const refs = jobOutputRefs({ output_refs: [{ type: 'image', asset_id: 'asset-1' }, 'loose-string', { asset_id: 7 }, null] });

    expect(refs).toEqual([{ type: 'image', asset_id: 'asset-1' }]);
    expect(jobOutputRefs(null)).toEqual([]);
    expect(jobOutputRefs({ output_refs: 'not-a-list' })).toEqual([]);
  });

  // Objects embedded in stream messages must use the field names of their OpenAPI components.
  it('names embedded fields as the contract does', () => {
    const barSchema = tradingStreamMessageSchema.options[0].shape.bar;
    const sessionSchema = chatStreamEventSchema.shape.session.unwrap();
    const messageSchema = sessionSchema.shape.messages.element;

    // binding_id is the stream's own field: the subscription it answers.
    expect(objectFields(barSchema).filter((field) => field !== 'binding_id' && !componentFields('MarketBar-Output').includes(field))).toEqual([]);
    expect(objectFields(sessionSchema).filter((field) => !componentFields('ChatSession').includes(field))).toEqual([]);
    expect(objectFields(messageSchema).filter((field) => !componentFields('ChatMessage').includes(field))).toEqual([]);
    expect(componentFields('ChatSession')).not.toHaveLength(0);
  });
});
