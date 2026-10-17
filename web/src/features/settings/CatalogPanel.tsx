import { useEffect, useState } from 'react';
import { omnixApiClient, type ProviderFacadePayload } from '../../api/client';
import { SettingsSection } from './SettingsPrimitives';

export function CatalogPanel() {
  const [catalog, setCatalog] = useState<ProviderFacadePayload>();
  useEffect(() => {
    let active = true;
    omnixApiClient.listModels().then((models) => {
      if (active) setCatalog(models);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  return <SettingsSection title="Runtime catalog" scope="status"><p>{catalog?.models.length ?? 0} models discovered.</p></SettingsSection>;
}
