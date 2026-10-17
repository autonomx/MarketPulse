export type SttJobFormValues = {
  providerId: string;
  audioAssetId: string;
  sourcePath: string;
  language: string;
};

export type SttModuleDefaults = {
  providerId: string;
  language: string;
  alignment: boolean;
  saveTranscript: boolean;
};

export function buildSttInputPayload(values: SttJobFormValues, defaults: SttModuleDefaults) {
  return {
    source_path: values.sourcePath || null,
    provider_id: values.providerId || defaults.providerId || null,
    language: values.language || defaults.language || null,
    alignment: defaults.alignment,
    save_transcript: defaults.saveTranscript,
  };
}

export function buildSttStages(defaults: SttModuleDefaults) {
  return [
    { id: 'transcribe', label: 'Transcribe audio', resource_class: 'gpu:stt' as const, status: 'queued' as const },
    ...(defaults.alignment
      ? [{ id: 'align', label: 'Align transcript', resource_class: 'cpu' as const, status: 'queued' as const }]
      : []),
    ...(defaults.saveTranscript
      ? [{ id: 'store-transcript', label: 'Store transcript asset', resource_class: 'cpu' as const, status: 'queued' as const }]
      : []),
  ];
}
