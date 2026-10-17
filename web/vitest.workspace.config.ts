import { mergeConfig } from 'vitest/config';

import webConfig from './vitest.config';

export default mergeConfig(webConfig, {
  test: {
    setupFiles: 'web/src/test/setup.ts',
  },
});
