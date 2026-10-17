export type LiveSttProtocol = 'legacy' | 'segmented-v1';

export type AcceptedVoiceFinal = {
  chatSessionId: string;
  sttSessionId: string;
  captureEpoch: string;
  segmentId: string;
  resultId: string;
  finalizeRequestId: string;
  sourceSequence: number;
  startSample: number;
  endSample: number;
  protocol: LiveSttProtocol;
  text: string;
  provider?: string;
  providerMetrics?: Record<string, number>;
  finalizeRequestedAtMs: number;
  receivedAtMs: number;
};

export type LiveFinalTerminalOutcome =
  | 'ignored'
  | 'material_acked'
  | 'observation_queued'
  | 'conversation_submitted'
  | 'control_executed'
  | 'failed';

export type LiveFinalRoutingResult = {
  outcome: LiveFinalTerminalOutcome;
  segmentId: string;
  sourceSequence: number;
  taskContractId: string;
  taskContractVersion: number;
  contextVersion?: number;
  errorCode?: string;
};

export function failedLiveFinalResult(
  final: Pick<AcceptedVoiceFinal, 'segmentId' | 'sourceSequence'>,
  taskContractId: string,
  taskContractVersion: number,
  errorCode: string,
): LiveFinalRoutingResult {
  return {
    outcome: 'failed',
    segmentId: final.segmentId,
    sourceSequence: final.sourceSequence,
    taskContractId,
    taskContractVersion,
    errorCode,
  };
}
