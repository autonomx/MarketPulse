/**
 * A screen from words (TVP-9.4): describe a screen and the research model proposes rules. The rules go into the
 * editor for the user to check and change; nothing runs until the user runs the screen.
 */
import { useState } from 'react';
import type { components } from './api/generated';
import type { ScreenerRuleInput } from './screenerRules';
import { tradingScannerApi } from './tradingScannerApi';

export type ScreenProposal = components['schemas']['ScreenProposal'];

export function ScreenFromWords({ onProposal }: { onProposal: (rules: ScreenerRuleInput[], interval: string) => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [proposal, setProposal] = useState<ScreenProposal | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const propose = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const next = await tradingScannerApi.propose(text);
      setProposal(next);
      onProposal(next.rules.map((rule) => ({ ...rule, role: rule.role ?? 'filter', source: rule.source ?? null })) as ScreenerRuleInput[], next.interval ?? '1d');
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="trading-screen-from-words" role="group" aria-label="Describe a screen">
      <form onSubmit={(event) => { event.preventDefault(); if (text.trim()) void propose(); }}>
        <input
          aria-label="Screen description"
          maxLength={500}
          placeholder="Describe a screen, e.g. daily gainers above 5% on twice the usual volume"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" disabled={busy || !text.trim()}>{busy ? 'Proposing…' : 'Propose rules'}</button>
      </form>
      {problem ? <p role="alert">{problem}</p> : null}
      {proposal ? (
        <p role="status">
          Proposed {proposal.rules.length} rule{proposal.rules.length === 1 ? '' : 's'} on {proposal.interval}: check them below, then save or run.
          {proposal.notes ? ` ${proposal.notes}` : ''}
          {proposal.unsupported?.length ? <span> Not covered: {proposal.unsupported.join('; ')}.</span> : null}
        </p>
      ) : null}
    </div>
  );
}
