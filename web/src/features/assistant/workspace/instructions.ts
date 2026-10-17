export type InstructionScope = 'global' | 'workspace' | 'project' | 'session';

export type InstructionRecord = {
  id: string;
  scope: InstructionScope;
  content: string;
  priority: number;
  enabled: boolean;
};

export function getEnabledInstructionRecords(records: InstructionRecord[]): InstructionRecord[] {
  return records.filter((record) => record.enabled);
}

export function sortInstructionRecords(records: InstructionRecord[]): InstructionRecord[] {
  return [...records].sort((left, right) => right.priority - left.priority || left.id.localeCompare(right.id));
}

export function getScopedInstructionRecords(
  records: InstructionRecord[],
  scopes: InstructionScope[],
): InstructionRecord[] {
  const allowed = new Set(scopes);
  return records.filter((record) => allowed.has(record.scope));
}
