import { apiRequest } from "../features/alerts/api/client";

type HealthResponse = {
  status: string;
};

export async function getHealth() {
  return apiRequest<HealthResponse>("/health");
}
