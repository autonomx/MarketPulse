import { useEffect } from 'react';
import { Link, useSearch } from '@tanstack/react-router';
import { AccountSection } from '../src/features/settings/AccountSection';
import { AiProvidersSettings } from '../src/features/settings/AiProvidersSettings';
import { SettingsActionHeader } from '../src/features/settings/SettingsActionHeader';
import { SettingsProfileProvider } from '../src/features/settings/SettingsProfileProvider';
import { SignedInSessionsSection } from '../src/features/settings/SignedInSessionsSection';
import { TradingMarketDataSettings } from '../src/features/settings/TradingMarketDataSettings';
import '../src/features/settings/SettingsComponents.css';
import '../src/features/settings/AiProvidersSettings.css';
import '../src/features/settings/IbkrSettings.css';
import { AppearanceSettings } from './AppearanceSettings';
import { ResearchHandoffSettings } from './ResearchHandoffSettings';

const CATEGORIES = [
  { id: 'appearance', label: 'Appearance' },
  { id: 'account', label: 'Account' },
  { id: 'trading-market-data', label: 'Market data & brokers' },
  { id: 'ai-providers', label: 'AI providers' },
  { id: 'research', label: 'Research handoffs' },
] as const;

/**
 * MarketPulse settings (roadmap MP-4.2): Omnix's own sections, one page, no Omnix settings workspace (which would
 * bundle every feature's settings). The trading workspace links here with `?category=`.
 */
export function SettingsPage() {
  const { category } = useSearch({ from: '/settings' });

  useEffect(() => {
    if (!category) return;
    document.getElementById(`settings-${category}`)?.scrollIntoView({ block: 'start' });
  }, [category]);

  return (
    <SettingsProfileProvider>
      <div className="mp-settings">
        <header className="mp-settings-header">
          <Link to="/trading" className="mp-settings-back">← Back to charts</Link>
          <h1>Settings</h1>
          <nav aria-label="Settings sections">
            {CATEGORIES.map((item) => (
              <a key={item.id} href={`#settings-${item.id}`} aria-current={item.id === category ? 'true' : undefined}>{item.label}</a>
            ))}
          </nav>
        </header>
        <SettingsActionHeader />
        <main className="mp-settings-main">
          <section id="settings-appearance" aria-label="Appearance">
            <AppearanceSettings />
          </section>
          <section id="settings-account" aria-label="Account">
            <AccountSection />
            <SignedInSessionsSection />
          </section>
          <section id="settings-trading-market-data" aria-label="Market data and brokers">
            <TradingMarketDataSettings />
          </section>
          <section id="settings-ai-providers" aria-label="AI providers">
            <AiProvidersSettings />
          </section>
          <section id="settings-research" aria-label="Research handoffs">
            <ResearchHandoffSettings />
          </section>
        </main>
      </div>
    </SettingsProfileProvider>
  );
}
