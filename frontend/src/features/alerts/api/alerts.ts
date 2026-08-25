import { apiRequest } from "../../alerts/api/client";
import type { Alert } from "../types/alert";

type AlertsResponse = {
  alerts: Alert[];
};

function isAlertsResponse(value: unknown): value is AlertsResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "alerts" in value &&
    Array.isArray((value as AlertsResponse).alerts)
  );
}

export async function getAlerts(): Promise<Alert[]> {
  const data = await apiRequest<unknown>("/internal/alerts");

  if (Array.isArray(data)) {
    if (data.length > 0 && isAlertsResponse(data[0])) {
      return data[0].alerts;
    }

    return data as Alert[];
  }

  if (isAlertsResponse(data)) {
    return data.alerts;
  }

  return [];
}

export type CreateAlertPayload = Alert;

export function createAlert(payload: CreateAlertPayload) {
  return apiRequest<Alert>("/internal/alerts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteAlert(alertId: string) {
  return apiRequest<void>(`/internal/alerts/${alertId}`, {
    method: "DELETE",
  });
}

export type UpdateAlertPayload = {
  expected_version: number;
  rule_id: string;
  rule_version: number;
  user_id: string;
  alert_type: string;
  filters_json: Record<string, string | number | boolean>;
  cooldown_seconds: number;
  channels: string[];
  enabled: boolean;
};

type UpdateAlertResponse = Alert | { alert: Alert };

export async function updateAlert(alert: Alert): Promise<Alert> {
  const data = await apiRequest<UpdateAlertResponse>(
    `/internal/alerts/${alert.alert_id}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expected_version: alert.version ?? 1,
        rule_id: alert.rule_id,
        rule_version: alert.rule_version,
        user_id: alert.user_id,
        alert_type: alert.alert_type,
        filters_json: alert.filters_json,
        cooldown_seconds: alert.cooldown_seconds,
        channels: alert.channels,
        enabled: alert.enabled,
      }),
    },
  );

  return "alert" in data ? data.alert : data;
}
