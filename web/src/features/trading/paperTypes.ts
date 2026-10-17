import type { components } from './api/generated';

// Paper trading payloads as the gateway sends them (WP-9.3).
export type PaperAccount = components['schemas']['PaperAccount-Output'];
export type PaperBalance = components['schemas']['PaperBalance-Output'];
export type PaperPosition = components['schemas']['PaperPosition-Output'];
export type PaperOrder = components['schemas']['PaperOrder-Output'];
export type PaperFill = components['schemas']['PaperFill-Output'];
export type PaperLedgerEntry = components['schemas']['PaperLedgerEntry-Output'];
export type PaperAccountSnapshot = components['schemas']['PaperAccountSnapshot-Output'];
export type PaperPositionProtection = components['schemas']['PaperPositionProtection'];
export type PaperRiskPreview = components['schemas']['PaperRiskPreview'];
export type PaperRiskOrderResult = components['schemas']['PaperRiskOrderResult'];
export type PaperRiskEntryMoveResult = components['schemas']['PaperRiskEntryMoveResult'];

export type PaperSide = PaperOrder['side'];
export type PaperOrderType = PaperOrder['order_type'];
export type PaperOrderStatus = PaperOrder['status'];
export type PaperTimeInForce = PaperOrder['time_in_force'];
export type PaperProtectionStatus = PaperPositionProtection['status'];

// What the UI sends.
export type PaperAccountCreateInput = components['schemas']['PaperAccountCreate'];
export type PaperAccountSettingsInput = components['schemas']['PaperAccountSettings'];
export type PaperOrderInput = components['schemas']['PaperOrderRequest'];
export type PaperProtectionInput = components['schemas']['PaperProtectionUpsert'];
export type PaperRiskPreviewInput = components['schemas']['PaperRiskPreviewRequest'];
export type PaperRiskOrderInput = components['schemas']['PaperRiskOrderRequest'];
export type PaperRiskEntryMoveInput = components['schemas']['PaperRiskEntryMoveRequest'];
