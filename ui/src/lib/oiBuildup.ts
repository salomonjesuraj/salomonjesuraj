import type { OIBuildupType } from '../types'

/** Shared OI-buildup classification -- originally SmartMoneyRadar.tsx's
 * own local constants, promoted here ("Unified Screener & Deep-Dive
 * Interactivity" sprint, 2026-08-28) once the new F&O Screener needed
 * the identical "which buildup types count as bullish/bearish smart
 * money" classification, rather than a second, possibly-drifting copy. */
export const BULL_OI: OIBuildupType[] = ['LONG_BUILDUP', 'SHORT_COVERING']
export const BEAR_OI: OIBuildupType[] = ['SHORT_BUILDUP', 'LONG_UNWINDING']

export const OI_LABEL: Record<OIBuildupType, string> = {
  LONG_BUILDUP: 'LONG BUILDUP',
  SHORT_COVERING: 'SHORT COVERING',
  SHORT_BUILDUP: 'SHORT BUILDUP',
  LONG_UNWINDING: 'LONG UNWINDING',
  NEUTRAL: 'NEUTRAL',
}
