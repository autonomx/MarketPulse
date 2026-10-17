/// <reference lib="webworker" />

import {
  indicatorDefaultBackgroundColor,
  indicatorOutputs,
  type CoreIndicatorInstance,
  type IndicatorOutput,
} from './coreIndicators';
import { calculateTradingViewBuiltInOutputs, isTradingViewBuiltInId } from './tradingViewBuiltIns';
import type { IndicatorWorkerRequest, IndicatorWorkerResponse } from './indicatorWorkerProtocol';
import { calculateWithSources } from './indicatorSources';

const workerScope: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope;

function styleOutputs(outputs: IndicatorOutput[], indicator: CoreIndicatorInstance): IndicatorOutput[] {
  if (!isTradingViewBuiltInId(indicator.id)) return outputs;
  return outputs
    .map((output) => ({
      ...output,
      visible: indicator.style?.plots?.[output.key] !== false,
      color: indicator.style?.colors?.[output.key] ?? output.color,
      lineStyle: indicator.style?.lineStyles?.[output.key] ?? output.lineStyle,
      lineWidth: indicator.style?.lineWidth ?? output.lineWidth,
      backgroundVisible: indicator.style?.backgroundVisible !== false,
      backgroundColor: indicator.style?.backgroundColor ?? output.backgroundColor ?? indicatorDefaultBackgroundColor(indicator.id),
      precision: indicator.style?.precision ?? output.precision,
      labelsOnPriceScale: indicator.style?.labelsOnPriceScale ?? output.labelsOnPriceScale ?? false,
      valuesInStatusLine: indicator.style?.valuesInStatusLine ?? output.valuesInStatusLine,
      inputsInStatusLine: indicator.style?.inputsInStatusLine ?? output.inputsInStatusLine,
    }))
    .filter((output) => output.visible !== false);
}

workerScope.addEventListener('message', (event: MessageEvent<IndicatorWorkerRequest>) => {
  const request = event.data;
  try {
    // Hidden indicators still compute: another indicator may read them (TVP-6.5); only their plots are left out.
    const outputs = calculateWithSources(
      request.bars,
      request.indicators.filter((indicator) => indicator.enabled),
      (bars, indicator) => (isTradingViewBuiltInId(indicator.id)
        ? calculateTradingViewBuiltInOutputs(bars, { ...indicator, session: request.session }, {
          compareBars: indicator.compareSymbol ? request.compareBars?.[indicator.compareSymbol] : undefined,
        }) as IndicatorOutput[]
        : indicatorOutputs(bars, indicator)),
      (raw, indicator) => (indicator.visible === false ? [] : styleOutputs(raw, indicator)),
    );
    const response: IndicatorWorkerResponse = { requestId: request.requestId, outputs };
    workerScope.postMessage(response);
  } catch (error) {
    const response: IndicatorWorkerResponse = {
      requestId: request.requestId,
      error: error instanceof Error ? error.message : String(error),
    };
    workerScope.postMessage(response);
  }
});

export {};
