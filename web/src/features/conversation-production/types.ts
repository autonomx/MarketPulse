export type ProductionAssetStatus =
  | 'draft'
  | 'queued'
  | 'generated'
  | 'in_review'
  | 'approved'
  | 'editable'
  | 'in_progress'
  | 'failed'
  | 'invalidated'
  | 'published';

export type ProductionStage =
  | 'research'
  | 'producer_plan'
  | 'canonical_script'
  | 'performance_script'
  | 'voice_takes'
  | 'mix'
  | 'renderer'
  | 'publish';

export type ReviewMode = 'auto' | 'manual';

export type ProductionGenerationStyle = 'automatic' | 'guided';

export interface ReviewPolicy {
  research: ReviewMode;
  producerPlan: ReviewMode;
  canonicalScript: ReviewMode;
  performanceScript: ReviewMode;
  voiceTakes: ReviewMode;
  mix: ReviewMode;
}

export interface ProductionConstraints {
  maxDurationSeconds: number;
  targetDurationSeconds: number;
  maxSpeakerTurnSeconds: number;
  citationRequired: boolean;
  familyFriendly: boolean;
  readingLevel: string;
  avoidTopics: string[];
  requiredTopics: string[];
  disallowedClaims: string[];
  tone: string;
  audience: string;
  language: string;
}

export type ProductionRendererKind =
  | 'podcast'
  | 'video'
  | 'rpg_dialogue'
  | 'ai_radio'
  | 'audiobook'
  | 'npc_dialogue'
  | 'interview'
  | 'training_content';

export interface ProductionRenderer {
  id: string;
  kind: ProductionRendererKind;
  label: string;
  route: string;
}

export interface ProductionAssetDependency {
  assetId: string;
  reason: string;
}

export interface ProductionAsset {
  id: string;
  stage: ProductionStage;
  label: string;
  status: ProductionAssetStatus;
  summary: string;
  version: number;
  dependencies: ProductionAssetDependency[];
  invalidatesStages: ProductionStage[];
  updatedAt: string;
}

export interface ProductionDAGNode {
  id: string;
  assetId: string;
  stage: ProductionStage;
  dependsOn: string[];
  canRunInParallel: boolean;
}

export interface ProductionDAGEdge {
  from: string;
  to: string;
  invalidation: 'none' | 'downstream' | 'selective';
}

export interface ProductionDAG {
  nodes: ProductionDAGNode[];
  edges: ProductionDAGEdge[];
}

export interface ConversationProduction {
  id: string;
  title: string;
  topic: string;
  brief: string;
  generationStyle: ProductionGenerationStyle;
  reviewPolicy: ReviewPolicy;
  constraints: ProductionConstraints;
  renderer: ProductionRenderer;
  assets: ProductionAsset[];
  dag: ProductionDAG;
  currentStage: ProductionStage;
  createdAt: string;
  updatedAt: string;
}

export const automaticReviewPolicy: ReviewPolicy = {
  research: 'auto',
  producerPlan: 'auto',
  canonicalScript: 'auto',
  performanceScript: 'auto',
  voiceTakes: 'auto',
  mix: 'auto',
};

export const guidedReviewPolicy: ReviewPolicy = {
  research: 'manual',
  producerPlan: 'manual',
  canonicalScript: 'manual',
  performanceScript: 'manual',
  voiceTakes: 'manual',
  mix: 'manual',
};
