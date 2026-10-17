import { audiobookModule } from '../features/audiobook/module';
import { chatbotModule } from '../features/assistant/module';
import { imageGenerationModule } from '../features/image-generation/module';
import { assetsModule, diagnosticsModule, jobsModule, modelsModule, providersModule, reportsModule } from '../features/platform/module';
import { podcastModule } from '../features/podcast/module';
import { rpgModule } from '../features/rpg/module';
import { settingsModule } from '../features/settings/module';
import { storytellerModule } from '../features/storyteller/module';
import { sttModule } from '../features/stt/module';
import { tradingModule } from '../features/trading/module';
import { voiceCloningModule } from '../features/voice-cloning/module';
import { voiceModule } from '../features/voice/module';

/**
 * Every workspace module, in navigation order (WP-9.7). Each line registers
 * one feature's manifest; ids, routes, navigation, the router, the view API
 * scope, icons and runtimes are derived from this list.
 */
export const moduleManifests = [
  rpgModule,
  chatbotModule,
  storytellerModule,
  audiobookModule,
  podcastModule,
  voiceModule,
  voiceCloningModule,
  sttModule,
  imageGenerationModule,
  tradingModule,
  providersModule,
  modelsModule,
  jobsModule,
  assetsModule,
  reportsModule,
  settingsModule,
  diagnosticsModule,
] as const;
