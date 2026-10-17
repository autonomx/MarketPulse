import { AiProvidersSettings } from './AiProvidersSettings';
import { AppearanceSettings } from './AppearanceSettings';
import { AssistantChatSettings } from './AssistantChatSettings';
import { VoiceAudioSettings } from './VoiceAudioSettings';
import { SETTINGS_CATEGORIES } from './settingsRegistry';
import { SettingsSection } from './SettingsPrimitives';
import { TradingMarketDataSettings } from './TradingMarketDataSettings';
import type { SettingsCategoryId } from './settingsTypes';

export function SettingsCategoryPanel({ categoryId }: { categoryId: SettingsCategoryId }) {
  if (categoryId === 'ai-providers') return <AiProvidersSettings />;
  if (categoryId === 'trading-market-data') return <TradingMarketDataSettings />;
  if (categoryId === 'appearance-accessibility') return <AppearanceSettings />;
  if (categoryId === 'assistant-chat') return <AssistantChatSettings />;
  if (categoryId === 'voice-audio') return <VoiceAudioSettings />;
  const category = SETTINGS_CATEGORIES.find((item) => item.id === categoryId) ?? SETTINGS_CATEGORIES[0]!;
  return <div><h2>{category.label}</h2><SettingsSection title="Configuration" scope="module">Planned.</SettingsSection></div>;
}
