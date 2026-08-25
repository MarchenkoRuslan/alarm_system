export type RuleType =
  | "trader_position_update"
  | "new_market_liquidity"
  | "volume_spike_5m";

export type Rule = {
  rule_id: string;
  rule_version: number;
  name: string;
  rule_type: RuleType;
};
