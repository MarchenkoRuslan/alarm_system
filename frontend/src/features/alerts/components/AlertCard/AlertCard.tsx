import styles from "./AlertCard.module.scss";
import type { Alert } from "../../types/alert";
import Button from "@/components/ui/Button/Button";
import StatusBadge from "@/components/StatusBadge/StatusBadge";
import { getAlertMetrics } from "../../utils/getAlertMetrics";
import { formatAlertTitle } from "../../utils/formatAlertTitle";

type AlertCardProps = {
  alert: Alert;
  onToggle: (alert: Alert) => void;
  onDelete: (alertId: string) => void;
};

export default function AlertCard({
  alert,
  onToggle,
  onDelete,
}: AlertCardProps) {
  const metrics = getAlertMetrics(alert);

  return (
    <article className={styles.alertCard}>
      <div className={styles.cardTop}>
        <StatusBadge variant={alert.enabled ? "success" : "danger"}>
          {alert.enabled ? "Active" : "Paused"}
        </StatusBadge>
      </div>

      <h2>{formatAlertTitle(alert.alert_type)}</h2>

      <div className={styles.metrics}>
        {metrics.map((metric) => (
          <div className={styles.metricCard} key={metric.label}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
          </div>
        ))}
      </div>

      <div className={styles.infoList}>
        <p className={styles.info}>
          <span className={styles.delivery}>Delivery</span>
          <strong>{alert.channels.join(", ")}</strong>
        </p>
      </div>

      <div className={styles.actions}>
        <Button
          type="button"
          variant="ghost"
          fullWidth
          onClick={() =>
            onToggle({
              ...alert,
              enabled: !alert.enabled,
            })
          }
        >
          {alert.enabled ? "Pause" : "Resume"}
        </Button>

        <Button
          type="button"
          variant="danger"
          fullWidth
          onClick={() => onDelete(alert.alert_id)}
        >
          Delete
        </Button>
      </div>
    </article>
  );
}
