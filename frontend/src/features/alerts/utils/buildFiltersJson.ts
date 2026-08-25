import type { Rule } from "../types/rule";
import { ALERT_DEFAULTS } from "../config/alertDefaults";

export function buildFiltersJson(
  ruleType: Rule["rule_type"],
  minSize: string,
): Record<string, string | number | boolean> {
  if (ruleType === "new_market_liquidity") {
    return {
      deferred_watch_ttl_hours:
        ALERT_DEFAULTS.newMarketLiquidity.deferredWatchTtlHours,

      target_liquidity_usd:
        Number(minSize) || ALERT_DEFAULTS.newMarketLiquidity.targetLiquidityUsd,
    };
  }

  if (ruleType === "volume_spike_5m") {
    return {
      return_5m_pct_min: Number(minSize) || 5,

      liquidity_usd_min: ALERT_DEFAULTS.volumeSpike5m.liquidityUsdMin,

      spread_bps_max: ALERT_DEFAULTS.volumeSpike5m.spreadBpsMax,
    };
  }

  if (ruleType === "trader_position_update") {
    return {
      return_1m_pct_min: Number(minSize) || 3,

      liquidity_usd_min: ALERT_DEFAULTS.traderPositionUpdate.liquidityUsdMin,

      spread_bps_max: ALERT_DEFAULTS.traderPositionUpdate.spreadBpsMax,
    };
  }

  return {};
}
