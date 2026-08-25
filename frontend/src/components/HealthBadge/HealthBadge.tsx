import styles from "./HealthBadge.module.scss";
import { useEffect, useState } from "react";
import StatusBadge from "@/components/StatusBadge/StatusBadge";
import { getHealth } from "@/api/health";

type HealthState = "checking" | "online" | "offline";

export default function HealthBadge() {
  const [status, setStatus] = useState<HealthState>("checking");

  useEffect(() => {
    async function checkHealth() {
      try {
        const data = await getHealth();

        setStatus(data.status === "ok" ? "online" : "offline");
      } catch {
        setStatus("offline");
      }
    }

    checkHealth();
  }, []);

  if (status === "checking") {
    return <StatusBadge variant="info">Checking API</StatusBadge>;
  }

  if (status === "online") {
    return (
      <StatusBadge variant="success" className={styles.healthBadge}>
        API Online
      </StatusBadge>
    );
  }

  return <StatusBadge variant="danger">API Offline</StatusBadge>;
}
