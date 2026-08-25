export const ALERT_DEFAULTS = {
  userId: "demo-user",
  channels: ["telegram"],
  cooldownSeconds: 180,

  newMarketLiquidity: {
    targetLiquidityUsd: 100000,
    deferredWatchTtlHours: 336,
  },

  volumeSpike5m: {
    liquidityUsdMin: 10000,
    spreadBpsMax: 500,
  },

  traderPositionUpdate: {
    liquidityUsdMin: 10000,
    spreadBpsMax: 500,
  },
};
