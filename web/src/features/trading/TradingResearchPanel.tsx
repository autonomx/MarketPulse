import { useState } from 'react';
import type { MarketResearchResult } from './researchTypes';
import { tradingResearchApi } from './tradingResearchApi';
import './TradingResearch.css';
import { PRODUCT_NAME } from '../../shared/productIdentity';

export function TradingResearchPanel({
  instrumentId,
  bindingId,
  interval,
}: {
  instrumentId: string;
  bindingId: string | null;
  interval: string;
}) {
  const [question, setQuestion] = useState(
    'Summarize the current technical structure, notable levels, and principal risks.',
  );
  const [barLimit, setBarLimit] = useState('120');
  const [levels, setLevels] = useState('');
  const [model, setModel] = useState('');
  const [result, setResult] = useState<MarketResearchResult | null>(null);
  const [status, setStatus] = useState<'ready' | 'loading' | 'error'>('ready');

  const generate = async () => {
    const numericLimit = Number(barLimit);
    const selectedLevels = levels
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean);
    if (!Number.isInteger(numericLimit) || numericLimit < 30 || numericLimit > 200) {
      setStatus('error');
      return;
    }
    setStatus('loading');
    try {
      setResult(await tradingResearchApi.generate({
        instrument_id: instrumentId,
        binding_id: bindingId,
        interval,
        bar_limit: numericLimit,
        question,
        selected_levels: selectedLevels,
        model: model || null,
      }));
      setStatus('ready');
    } catch {
      setStatus('error');
    }
  };

  return (
    <section className="trading-research-panel" aria-label="Read-only market research" data-status={status}>
      <header>
        <div>
          <strong>AI market research</strong>
          <small>Read-only · normalized Trading data only</small>
        </div>
        <span>{status}</span>
      </header>
      <p>
        Uses the currently configured {PRODUCT_NAME} provider. It cannot place orders, create alerts,
        or change paper accounts.
      </p>
      <div className="trading-research-form">
        <label>
          Research question
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={800} />
        </label>
        <label>
          Finalized bars
          <input inputMode="numeric" value={barLimit} onChange={(event) => setBarLimit(event.target.value)} />
        </label>
        <label>
          Selected levels
          <input value={levels} onChange={(event) => setLevels(event.target.value)} placeholder="65000, 62000" />
        </label>
        <label>
          Model override
          <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Use configured model" />
        </label>
        <button type="button" disabled={status === 'loading' || !question.trim()} onClick={() => void generate()}>
          Generate read-only research
        </button>
      </div>

      {result ? (
        <article className="trading-research-result">
          <h4>{result.summary}</h4>
          <section>
            <strong>Observations</strong>
            <ul>{result.observations.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
          <section>
            <strong>Risks</strong>
            <ul>{result.risks.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
          <dl>
            <div><dt>Confidence</dt><dd>{result.confidence}</dd></div>
            <div><dt>Provider / model</dt><dd>{result.provider} / {result.model}</dd></div>
            <div><dt>Feed</dt><dd>{result.source.provider} · {result.source.freshness_mode}</dd></div>
            <div><dt>As of</dt><dd>{new Date(result.source.as_of).toLocaleString()}</dd></div>
            <div><dt>Bars</dt><dd>{result.source.bar_count} · {result.source.interval}</dd></div>
            <div><dt>Dataset fingerprint</dt><dd><code>{result.source.dataset_fingerprint}</code></dd></div>
            <div><dt>Formula</dt><dd>{result.source.formula_version}</dd></div>
          </dl>
          <footer>{result.disclaimer}</footer>
        </article>
      ) : null}
    </section>
  );
}
