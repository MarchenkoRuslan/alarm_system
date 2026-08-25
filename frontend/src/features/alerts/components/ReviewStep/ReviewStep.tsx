import type { Rule } from "../../types/rule";
import styles from "./ReviewStep.module.scss";

type ReviewStepProps = {
  selectedRule?: Rule;
  side: string;
  minSize: string;
};

export default function ReviewStep({
  selectedRule,
  side,
  minSize,
}: ReviewStepProps) {
  return (
    <>
      <h2>Review alert</h2>
      <p className={styles.description}>
        Check your selected values before creating the alert.
      </p>

      <div className={styles.reviewCard}>
        <div>
          <span>Trigger</span>
          <strong>{selectedRule?.name ?? "Not selected"}</strong>
        </div>

        <div>
          <span>Type</span>
          <strong>{selectedRule?.rule_type.replaceAll("_", " ") ?? "—"}</strong>
        </div>

        <div>
          <span>Side</span>
          <strong>{side}</strong>
        </div>

        <div>
          <span>Min size</span>
          <strong>{minSize || "—"}</strong>
        </div>

        <div>
          <span>Delivery</span>
          <strong>Telegram</strong>
        </div>
      </div>
    </>
  );
}
