import type { Alert } from "../types/alert";

type AlertMetric = {
  label: string;
  value: string;
};

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

export function getAlertMetrics(alert: Alert): AlertMetric[] {
  const filters = alert.filters_json;

  if (alert.alert_type === "new_market_liquidity") {
    return [
      {
        label: "Liquidity target",
        value: formatCurrency(Number(filters.target_liquidity_usd)),
      },
      {
        label: "Watch window",
        value: `${filters.deferred_watch_ttl_hours}h`,
      },
    ];
  }

  if (alert.alert_type === "volume_spike_5m") {
    return [
      {
        label: "5m spike",
        value: `${filters.return_5m_pct_min}%`,
      },
      {
        label: "Min liquidity",
        value: formatCurrency(Number(filters.liquidity_usd_min)),
      },
    ];
  }

  if (alert.alert_type === "trader_position_update") {
    return [
      {
        label: "1m move",
        value: `${filters.return_1m_pct_min}%`,
      },
      {
        label: "Max spread",
        value: `${filters.spread_bps_max} bps`,
      },
    ];
  }

  return [];
}
