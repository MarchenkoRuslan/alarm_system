import { apiRequest } from "../api/client";
import type { ChannelBinding } from "../types/channelBinding";

type ChannelBindingsResponse = {
  bindings: ChannelBinding[];
};

export async function getChannelBindings(): Promise<ChannelBinding[]> {
  const data = await apiRequest<ChannelBindingsResponse>(
    "/internal/channel-bindings"
  );

  return data.bindings ?? [];
}

export type CreateChannelBindingPayload = {
  binding_id: string;
  channel: "telegram";
  destination: string;
  is_verified: boolean;
  settings_json: Record<string, string | number | boolean>;
  user_id: string;
};

type CreateChannelBindingResponse =
  | ChannelBinding
  | { binding: ChannelBinding };

export async function createChannelBinding(
  payload: CreateChannelBindingPayload
): Promise<ChannelBinding> {
  const data = await apiRequest<CreateChannelBindingResponse>(
    "/internal/channel-bindings",
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );

  return "binding" in data ? data.binding : data;
}

export function deleteChannelBinding(bindingId: string) {
  return apiRequest<void>(`/internal/channel-bindings/${bindingId}`, {
    method: "DELETE",
  });
}
