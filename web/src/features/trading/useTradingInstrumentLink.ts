import { useEffect, useState } from 'react';
import { useSearchParam } from '../../shared/useSearchParam';

type InstrumentLinkOptions = {
  /** Whether the saved workspace has loaded; a link applies only after it, so the saved state does not overwrite it. */
  workspaceHydrated: boolean;
  instruments: Array<{ instrument_id: string }> | undefined;
  activeInstrumentId: string;
  /** Shows an instrument in the active chart. */
  showInstrument: (instrumentId: string) => void;
};

/**
 * The active chart's instrument is in the URL (?instrument=), so a chart can
 * be linked to (WP-9.6). A link to a known instrument applies once, after
 * the saved workspace has loaded; after that the URL follows the chart.
 */
export function useTradingInstrumentLink({ workspaceHydrated, instruments, activeInstrumentId, showInstrument }: InstrumentLinkOptions): void {
  const [linkedInstrumentId, setLinkedInstrumentId] = useSearchParam('instrument');
  const [applied, setApplied] = useState(false);
  useEffect(() => {
    if (applied || !workspaceHydrated || !instruments) return;
    setApplied(true);
    const known = linkedInstrumentId && instruments.some((instrument) => instrument.instrument_id === linkedInstrumentId);
    if (known && linkedInstrumentId !== activeInstrumentId) showInstrument(linkedInstrumentId);
  }, [activeInstrumentId, applied, instruments, linkedInstrumentId, showInstrument, workspaceHydrated]);
  useEffect(() => {
    if (applied) setLinkedInstrumentId(activeInstrumentId);
  }, [activeInstrumentId, applied, setLinkedInstrumentId]);
}
