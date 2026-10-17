import type { components } from './api/generated';
export type TradingScannerDefinition = components['schemas']['TradingScannerDefinition-Output'];
/** A definition as the UI sends it; the gateway fills defaults. */
export type TradingScannerDefinitionInput = components['schemas']['TradingScannerDefinition-Input'];
export type TradingScannerRule = TradingScannerDefinition['rules'][number];
export type TradingScannerMetric = TradingScannerRule['metric'];
export type TradingScannerOperator = TradingScannerRule['operator'];
export type TradingScannerRunStatus = components['schemas']['TradingScannerRun']['status'];

export type TradingScannerRun = components['schemas']['TradingScannerRun'];

export type TradingScannerResult = components['schemas']['TradingScannerResult'];
