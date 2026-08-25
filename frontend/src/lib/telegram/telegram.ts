import { init, miniApp, themeParams, viewport } from "@telegram-apps/sdk-react";
import { ALERT_DEFAULTS } from "@/features/alerts/config/alertDefaults";

export type TelegramUser = {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
};

export function initTelegram() {
  const webApp = window.Telegram?.WebApp;

  if (!webApp?.initData) {
    return;
  }

  try {
    init();

    miniApp.mount();
    themeParams.mount();
    viewport.mount();

    miniApp.ready();
  } catch {
    return;
  }
}

export function getTelegramUser(): TelegramUser | null {
  const user = window.Telegram?.WebApp?.initDataUnsafe?.user;

  if (!user || typeof user.id !== "number") {
    return null;
  }

  return user;
}

export function getTelegramUserId() {
  const user = getTelegramUser();

  return user ? String(user.id) : ALERT_DEFAULTS.userId;
}

export function triggerTelegramHaptic(
  type: "success" | "error" | "warning" = "success"
) {
  const hapticFeedback = window.Telegram?.WebApp?.HapticFeedback;

  if (!hapticFeedback) {
    return;
  }

  hapticFeedback.notificationOccurred(type);
}
