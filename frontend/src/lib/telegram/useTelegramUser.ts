import { useMemo } from "react";
import { getTelegramUser } from "./telegram";

export function useTelegramUser() {
  return useMemo(() => getTelegramUser(), []);
}
