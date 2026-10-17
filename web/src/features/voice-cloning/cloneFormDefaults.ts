export type CloneFormValues = {
  providerId: string;
  sampleAssetId: string;
  profileName: string;
  referenceText: string;
  language: string;
  quality: string;
};

export type CloneModuleDefaults = {
  providerId: string;
  language: string;
  quality: string;
};

export function cloneFormDefaults(defaults: CloneModuleDefaults): CloneFormValues {
  return {
    providerId: defaults.providerId,
    sampleAssetId: '',
    profileName: '',
    referenceText: '',
    language: defaults.language,
    quality: defaults.quality,
  };
}

export function buildProfileInput(values: CloneFormValues, defaults: CloneModuleDefaults) {
  return {
    provider_id: values.providerId || defaults.providerId || null,
    profile_name: values.profileName,
    reference_text: values.referenceText || null,
    language: values.language || defaults.language || null,
    quality: values.quality || defaults.quality || null,
  };
}
