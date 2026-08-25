import styles from "./DeliveryStep.module.scss";
import OptionCard from "@/components/OptionCard/OptionCard";

export default function DeliveryStep() {
  return (
    <>
      <h2>Choose delivery method</h2>
      <p className={styles.description}>
        Select where alert notifications should be sent.
      </p>

      <div className={styles.optionList}>
        <OptionCard
          icon="✈️"
          title="Telegram"
          description="Receive notifications inside Telegram."
          selected
        />
      </div>
    </>
  );
}
