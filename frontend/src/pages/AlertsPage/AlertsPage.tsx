import styles from "./AlertsPage.module.scss";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Button from "@/components/ui/Button/Button";
import EmptyState from "@/components/EmptyState/EmptyState";
import HealthBadge from "@/components/HealthBadge/HealthBadge";
import PageHeader from "@/components/PageHeader/PageHeader";
import PageShell from "@/components/PageShell/PageShell";
import StatusMessage from "@/components/StatusMessage/StatusMessage";
import TelegramUserBadge from "@/components/TelegramUserBadge/TelegramUserBadge";
import {
  createChannelBinding,
  deleteChannelBinding,
  getChannelBindings,
} from "@/features/alerts/api/channelBindings";
import {
  deleteAlert,
  getAlerts,
  updateAlert,
} from "@/features/alerts/api/alerts";
import AlertCard from "@/features/alerts/components/AlertCard/AlertCard";
import ChannelBindingsCard from "@/features/alerts/components/ChannelBindingsCard/ChannelBindingsCard";
import { ALERT_DEFAULTS } from "@/features/alerts/config/alertDefaults";
import type { Alert } from "@/features/alerts/types/alert";
import type { ChannelBinding } from "@/features/alerts/types/channelBinding";
import { useTelegramUser } from "@/lib/telegram/useTelegramUser";
import { triggerTelegramHaptic } from "@/lib/telegram/telegram";

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [bindings, setBindings] = useState<ChannelBinding[]>([]);
  const [isBindingsLoading, setIsBindingsLoading] = useState(true);
  const [bindingsError, setBindingsError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const telegramUser = useTelegramUser();
  const userId = telegramUser ? String(telegramUser.id) : ALERT_DEFAULTS.userId;
  const telegramDestination = userId;

  const hasTelegramBinding = bindings.some(
    (binding) =>
      binding.channel === "telegram" &&
      binding.destination === telegramDestination &&
      binding.is_verified
  );

  useEffect(() => {
    async function loadAlerts() {
      try {
        const data = await getAlerts();
        const userAlerts = data.filter((alert) => alert.user_id === userId);

        setAlerts(userAlerts);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Unknown error";

        console.error(message);
        setError(message);
      } finally {
        setIsLoading(false);
      }
    }

    loadAlerts();
  }, [userId]);

  useEffect(() => {
    async function loadBindings() {
      try {
        const data = await getChannelBindings();
        const userBindings = data.filter(
          (binding) => binding.user_id === userId
        );

        setBindings(userBindings);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Unknown error";
        setBindingsError(message);
      } finally {
        setIsBindingsLoading(false);
      }
    }

    loadBindings();
  }, [userId]);

  async function handleDeleteAlert(alertId: string) {
    try {
      await deleteAlert(alertId);

      setAlerts((prev) => prev.filter((alert) => alert.alert_id !== alertId));
      triggerTelegramHaptic("success");
    } catch (error) {
      triggerTelegramHaptic("error");
      console.error(error);
    }
  }

  async function handleToggleAlert(updatedAlert: Alert) {
    try {
      const savedAlert = await updateAlert(updatedAlert);

      setAlerts((prev) =>
        prev.map((alert) =>
          alert.alert_id === savedAlert.alert_id ? savedAlert : alert
        )
      );
      triggerTelegramHaptic("success");
    } catch (error) {
      triggerTelegramHaptic("error");
      console.error(error);
    }
  }

  useEffect(() => {
    if (!telegramUser || isBindingsLoading || hasTelegramBinding) {
      return;
    }

    async function connectTelegram() {
      try {
        const binding = await createChannelBinding({
          binding_id: `tg-${userId}-${crypto.randomUUID()}`,
          channel: "telegram",
          destination: telegramDestination,
          is_verified: true,
          settings_json: {},
          user_id: userId,
        });

        setBindings((prev) => [...prev, binding]);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Unknown error";
        setBindingsError(message);
      }
    }

    void connectTelegram();
  }, [
    telegramUser,
    isBindingsLoading,
    hasTelegramBinding,
    telegramDestination,
    userId,
  ]);

  async function handleDeleteBinding(bindingId: string) {
    try {
      await deleteChannelBinding(bindingId);

      setBindings((prev) =>
        prev.filter((binding) => binding.binding_id !== bindingId)
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      setBindingsError(message);
    }
  }

  return (
    <PageShell>
      <PageHeader
        eyebrow="Telegram Alerts"
        title="Manage your alerts"
        description="Create, monitor and control your market alerts from one clean dashboard."
        action={
          <Button
            type="button"
            variant="primary"
            fullWidth
            onClick={() => navigate("/create")}
          >
            Create alert
          </Button>
        }
      />
      <div className={styles.badges}>
        {import.meta.env.DEV && <HealthBadge />}
        <TelegramUserBadge />
      </div>
      <ChannelBindingsCard
        bindings={bindings}
        isLoading={isBindingsLoading}
        error={bindingsError}
        onDelete={handleDeleteBinding}
      />

      {isLoading && (
        <StatusMessage variant="loading">Loading alerts...</StatusMessage>
      )}

      {error && <StatusMessage variant="error">{error}</StatusMessage>}

      {!isLoading && !error && alerts.length === 0 && (
        <EmptyState
          title="No alerts yet"
          description="Create your first alert to start tracking important market events."
        />
      )}

      {!isLoading && !error && alerts.length > 0 && (
        <section className={styles.alertsGrid}>
          {alerts.map((alert) => (
            <AlertCard
              alert={alert}
              key={alert.alert_id}
              onDelete={handleDeleteAlert}
              onToggle={handleToggleAlert}
            />
          ))}
        </section>
      )}
    </PageShell>
  );
}
