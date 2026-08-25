export {};

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        HapticFeedback?: {
          notificationOccurred: (type: "success" | "error" | "warning") => void;
        };
        initData?: string;
        initDataUnsafe?: {
          user?: {
            id: number;
            first_name?: string;
            last_name?: string;
            username?: string;
            language_code?: string;
          };
        };
        ready?: () => void;
      };
    };
  }
}
