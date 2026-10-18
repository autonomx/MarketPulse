import type { ReactNode } from 'react';
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { AccountSection } from '../src/features/settings/AccountSection';
import { AiProvidersSettings } from '../src/features/settings/AiProvidersSettings';
import { SettingsProfileProvider } from '../src/features/settings/SettingsProfileProvider';
import { SignedInSessionsSection } from '../src/features/settings/SignedInSessionsSection';
import { TradingMarketDataSettings } from '../src/features/settings/TradingMarketDataSettings';
import '../src/features/settings/SettingsComponents.css';
import '../src/features/settings/AiProvidersSettings.css';
import '../src/features/settings/IbkrSettings.css';
import { AppearanceSettings } from './AppearanceSettings';
import { ResearchHandoffSettings } from './ResearchHandoffSettings';
import { SettingsSaveBar } from './SettingsSaveBar';

interface Category {
  id: string;
  label: string;
  description: string;
  icon: ReactNode;
  content: () => ReactNode;
}

const icon = (path: string) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={path} />
  </svg>
);

const CATEGORIES: Category[] = [
  {
    id: 'appearance',
    label: 'Appearance',
    description: 'How MarketPulse looks on this computer.',
    icon: icon('M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9Z'),
    content: () => <AppearanceSettings />,
  },
  {
    id: 'account',
    label: 'Account',
    description: 'Your sign-in, password and Google account, and where you are signed in.',
    icon: icon('M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 9a7 7 0 0 1 14 0'),
    content: () => (
      <>
        <AccountSection />
        <SignedInSessionsSection />
      </>
    ),
  },
  {
    id: 'trading-market-data',
    label: 'Market data & brokers',
    description: 'Where prices, fundamentals and events come from, and the keys and connections that add more.',
    icon: icon('M4 19V5m0 14h16M8 15l3-4 3 2 5-6'),
    content: () => <TradingMarketDataSettings />,
  },
  {
    id: 'ai-providers',
    label: 'AI providers',
    description: 'The models behind AI analysis and research, and how work is routed between them.',
    icon: icon('M12 3v3m0 12v3M3 12h3m12 0h3M7 7l2 2m6 6 2 2M7 17l2-2m6-6 2-2M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z'),
    content: () => <AiProvidersSettings />,
  },
  {
    id: 'research',
    label: 'Research handoffs',
    description: 'Where the premarket research handoffs come from.',
    icon: icon('M5 4h10l4 4v12H5zM14 4v5h5M8 13h8M8 17h5'),
    content: () => <ResearchHandoffSettings />,
  },
];

/**
 * MarketPulse settings (roadmap MP-4.2): Omnix's own sections in MarketPulse's frame, one category at a time, without
 * Omnix's settings workspace (which would bundle every feature's settings). `?category=` opens one; the trading
 * workspace links to Market data & brokers that way.
 */
export function SettingsPage() {
  const { category } = useSearch({ from: '/settings' });
  const navigate = useNavigate();
  const active = CATEGORIES.find((item) => item.id === category) ?? CATEGORIES[0];

  return (
    <SettingsProfileProvider>
      <div className="mp-settings">
        <aside className="mp-settings-sidebar">
          <Link to="/trading" className="mp-settings-back">
            {icon('M15 18l-6-6 6-6')}
            <span>Back to charts</span>
          </Link>
          <h1>Settings</h1>
          <nav aria-label="Settings sections">
            {CATEGORIES.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-current={item.id === active.id ? 'page' : undefined}
                onClick={() => void navigate({ to: '/settings', search: { category: item.id }, replace: true })}
              >
                {item.icon}
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
        </aside>
        <main className="mp-settings-pane" aria-labelledby="mp-settings-title">
          <header className="mp-settings-pane-header">
            <h2 id="mp-settings-title">{active.label}</h2>
            <p>{active.description}</p>
          </header>
          <section id={`settings-${active.id}`} className="mp-settings-content" aria-label={active.label}>
            {active.content()}
          </section>
          <SettingsSaveBar />
        </main>
      </div>
    </SettingsProfileProvider>
  );
}
