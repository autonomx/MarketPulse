import { useState } from 'react';
import type { TradingCommandAvailability } from './commands/tradingCommands';
import { useInstallApp } from './installedApp';
import { PRODUCT_NAME } from '../../shared/productIdentity';

/** In the browser: why some keys are marked "app", and the install button when the browser offers one (TVP-4.5). */
export function InstallAppNote({ availability }: { availability: TradingCommandAvailability }) {
  const install = useInstallApp();
  const [outcome, setOutcome] = useState<string | null>(null);
  const [prompting, setPrompting] = useState(false);
  if (availability === 'installed') return null;
  const run = (start: () => Promise<boolean>) => {
    setPrompting(true);
    void start().then((accepted) => setOutcome(accepted ? `Installed: open ${PRODUCT_NAME} Trading from your apps.` : 'Not installed.')).finally(() => setPrompting(false));
  };
  return (
    <p className="trading-install-app-note">
      Keys marked “app” (Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+1…9) belong to the browser; they work in the installed {PRODUCT_NAME} Trading app.
      {install ? (
        <button type="button" onClick={() => run(install)}>Install app</button>
      ) : outcome || prompting ? null : <span> Install it from the browser’s menu (Install {PRODUCT_NAME} Trading).</span>}
      {outcome ? <span role="status"> {outcome}</span> : null}
    </p>
  );
}
