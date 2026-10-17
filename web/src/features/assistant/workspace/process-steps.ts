export type ProcessStage = 'input' | 'context' | 'run' | 'output';

export type ProcessStep = {
  id: string;
  stage: ProcessStage;
  completed: boolean;
};

export function createProcessStep(step: ProcessStep): ProcessStep {
  return { ...step };
}

export function getCompletedProcessStages(steps: ProcessStep[]): ProcessStage[] {
  return Array.from(new Set(steps.filter((step) => step.completed).map((step) => step.stage)));
}

export function isProcessComplete(steps: ProcessStep[]): boolean {
  const completed = new Set(getCompletedProcessStages(steps));
  return ['input', 'context', 'run', 'output'].every((stage) => completed.has(stage as ProcessStage));
}
