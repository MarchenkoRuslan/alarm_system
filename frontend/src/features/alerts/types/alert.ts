export type Alert = {
  alert_id: string;
  alert_type: string;
  channels: string[];
  cooldown_seconds: number;
  enabled: boolean;
  filters_json: Record<string, string | number | boolean | string[]>;
  rule_id: string;
  rule_version: number;
  user_id: string;
  version?: number;
  created_at?: string;
};
