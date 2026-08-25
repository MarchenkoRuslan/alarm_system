export type ChannelBinding = {
  binding_id: string;
  channel: "telegram" | string;
  destination: string;
  is_verified: boolean;
  settings_json: Record<string, string | number | boolean>;
  user_id: string;
};
