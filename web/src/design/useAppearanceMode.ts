import { useEffect, useState } from 'react';
import { APPEARANCE_CHANGE_EVENT } from '../events/bus';
import { currentAppearanceMode, type OmnixAppearanceChangeDetail } from './appearanceEffects';

/** The page's light level, following every saved change (title bar, settings, the Omnix top bar). */
export function useAppearanceMode() {
  const [state, setState] = useState(currentAppearanceMode);

  useEffect(() => {
    const sync = (event: Event) => {
      const detail = (event as CustomEvent<OmnixAppearanceChangeDetail>).detail;
      if (detail) setState({ mode: detail.mode, resolvedMode: detail.resolvedMode });
    };
    window.addEventListener(APPEARANCE_CHANGE_EVENT, sync);
    return () => window.removeEventListener(APPEARANCE_CHANGE_EVENT, sync);
  }, []);

  return state;
}
