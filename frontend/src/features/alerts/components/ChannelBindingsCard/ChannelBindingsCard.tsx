import styles from "./ChannelBindingsCard.module.scss";
import Button from "@/components/ui/Button/Button";
import StatusMessage from "@/components/StatusMessage/StatusMessage";
import EmptyState from "@/components/EmptyState/EmptyState";
import StatusBadge from "@/components/StatusBadge/StatusBadge";
import type { ChannelBinding } from "../../types/channelBinding";

type ChannelBindingsCardProps = {
  bindings: ChannelBinding[];
  isLoading: boolean;
  error: string;
  onDelete: (bindingId: string) => void;
};

export default function ChannelBindingsCard({
  bindings,
  isLoading,
  error,
  onDelete,
}: ChannelBindingsCardProps) {
  const hasTelegramBinding = bindings.some(
    (binding) => binding.channel === "telegram" && binding.is_verified
  );

  return (
    <section className={styles.card}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Delivery</p>
          <h2 className={styles.title}>Telegram connection</h2>
        </div>

        <StatusBadge variant={hasTelegramBinding ? "success" : "danger"}>
          {hasTelegramBinding ? "Connected" : "Not connected"}
        </StatusBadge>
      </header>

      {isLoading && (
        <StatusMessage variant="loading">Loading bindings...</StatusMessage>
      )}

      {error && <StatusMessage variant="error">{error}</StatusMessage>}

      {!isLoading && !error && bindings.length === 0 && (
        <EmptyState
          title="Telegram is not connected yet"
          description="Open this app inside Telegram to connect delivery automatically."
        />
      )}

      {!isLoading && !error && bindings.length > 0 && (
        <div className={styles.list}>
          {bindings.map((binding, index) => (
            <article
              className={styles.bindingItem}
              key={`${binding.binding_id}-${index}`}
            >
              <div>
                <strong className={styles.channel}>{binding.channel}</strong>
                <p className={styles.destination}>{binding.destination}</p>
              </div>

              <Button
                type="button"
                variant="danger"
                onClick={() => onDelete(binding.binding_id)}
                className={styles.button}
              >
                Remove
              </Button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
