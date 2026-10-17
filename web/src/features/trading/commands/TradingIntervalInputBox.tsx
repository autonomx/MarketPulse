import { createPortal } from 'react-dom';
import { useEffect, useRef, useState } from 'react';
import { resolveTradingIntervalInput } from '../tradingIntervalInput';
import { useModalDialog } from './useModalDialog';
import './TradingKeyboard.css';

/**
 * The box that opens when you type a digit or comma on the chart. Enter
 * applies the interval, Escape closes and returns focus. Unknown intervals
 * stay in the box with an error.
 */
export function TradingIntervalInputBox({
  initialText,
  supportedIntervals,
  onApply,
  onClose,
}: {
  /** What was typed to open the box; null while the box is closed. */
  initialText: string | null;
  supportedIntervals: readonly string[];
  onApply: (interval: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const open = initialText !== null;
  const onDialogKeyDown = useModalDialog(open, dialogRef, onClose);

  useEffect(() => {
    if (initialText === null) return;
    setText(initialText);
    setError(null);
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    input.setSelectionRange(initialText.length, initialText.length);
  }, [initialText]);

  if (!open || typeof document === 'undefined') return null;

  const apply = () => {
    const result = resolveTradingIntervalInput(text, supportedIntervals);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onClose();
    onApply(result.interval);
  };

  return createPortal(
    <div className="trading-interval-box-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="trading-interval-box" role="dialog" aria-modal="true" aria-labelledby="trading-interval-box-title" onKeyDown={onDialogKeyDown}>
        <h2 id="trading-interval-box-title">Change interval</h2>
        <input
          ref={inputRef}
          aria-label="Interval"
          aria-describedby={error ? 'trading-interval-box-error' : 'trading-interval-box-hint'}
          aria-invalid={error ? true : undefined}
          value={text}
          onChange={(event) => { setText(event.target.value); setError(null); }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              apply();
            }
          }}
        />
        {error
          ? <p id="trading-interval-box-error" className="trading-interval-box-error" role="alert">{error}</p>
          : <p id="trading-interval-box-hint">For example 5, 15, 1h, 1D, 1W, 1M, 100t. Enter to apply.</p>}
      </section>
    </div>,
    document.body,
  );
}
