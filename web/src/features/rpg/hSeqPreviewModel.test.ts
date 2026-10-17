import { describe, expect, it } from 'vitest';
import { hermesSequencePreviewModel } from './hermesSequencePreviewModel';

describe('preview model mapper', () => {
  it('maps populated input', () => {
    const result = hermesSequencePreviewModel({
      ok: true,
      validation: { ok: true, errors: [] },
      gate: { allowed: true, blocked_count: 0, decisions: [{ item_id: 'item-1', allowed: true }] },
      sequence: {
        sequence_id: 'seq-1',
        objective: 'Room details',
        domain: 'rpg',
        state_owner: 'rpg_sim',
        risk: 'low',
        items: [{ item_id: 'item-1', statement: 'inspect room', user_gate: false }],
      },
    });

    expect(result?.sequence_id).toBe('seq-1');
    expect(result?.review_status).toBe('ready');
    expect(result?.validation_status).toBe('valid');
    expect(result?.gate_status).toBe('ready');
    expect(result?.first_usable_command).toBe('inspect room');
    expect(result?.items?.[0]?.statement).toBe('inspect room');
    expect(result?.items?.[0]?.user_gate).toBe(false);
  });

  it('maps blocked gate decisions', () => {
    const result = hermesSequencePreviewModel({
      ok: false,
      validation: { ok: true, errors: [] },
      gate: {
        allowed: false,
        blocked_count: 1,
        decisions: [{ item_id: 'item-1', allowed: false, reason: 'stateful_statement' }],
      },
      sequence: {
        sequence_id: 'seq-1',
        objective: 'Buy rope',
        items: [{ item_id: 'item-1', statement: 'buy rope', user_gate: false }],
      },
    });

    expect(result?.review_status).toBe('blocked');
    expect(result?.blocked_reason).toBe('stateful_statement');
    expect(result?.items?.[0]?.gate_allowed).toBe(false);
    expect(result?.items?.[0]?.gate_reason).toBe('stateful_statement');
  });

  it('maps checkpoint policy reasons', () => {
    const result = hermesSequencePreviewModel({
      ok: false,
      validation: { ok: true, errors: [] },
      gate: { allowed: true, blocked_count: 0, decisions: [{ item_id: 'item-1', allowed: true }] },
      checkpoint: { requires_checkpoint: true, reason: 'combat_action' },
      sequence: {
        sequence_id: 'seq-1',
        objective: 'Fight',
        items: [{ item_id: 'item-1', statement: 'attack the bandit', user_gate: false }],
      },
    });

    expect(result?.review_status).toBe('blocked');
    expect(result?.blocked_reason).toBe('combat_action');
  });

  it('maps loop guard stop reasons', () => {
    const result = hermesSequencePreviewModel({
      ok: false,
      validation: { ok: true, errors: [] },
      gate: { allowed: true, blocked_count: 0, decisions: [] },
      loop_guard: { ok: false, stop_reason: 'duplicate_command' },
      sequence: {
        sequence_id: 'seq-1',
        objective: 'Loop',
        items: [{ item_id: 'item-1', statement: 'look around', user_gate: false }],
      },
    });

    expect(result?.review_status).toBe('blocked');
    expect(result?.blocked_reason).toBe('duplicate_command');
  });

  it('maps invalid sequences with validation errors', () => {
    const result = hermesSequencePreviewModel({
      ok: false,
      validation: { ok: false, errors: ['missing_objective', 'missing_items'] },
      sequence: { sequence_id: 'seq-1', items: [] },
      gate: null,
    });

    expect(result?.review_status).toBe('invalid');
    expect(result?.validation_status).toBe('2 issues');
    expect(result?.validation_errors).toEqual(['missing_objective', 'missing_items']);
  });

  it('returns null for empty input', () => {
    expect(hermesSequencePreviewModel(null)).toBeNull();
    expect(hermesSequencePreviewModel({ ok: false })).toBeNull();
  });
});
