import type { ReactNode } from "react";
import styles from "./OptionCard.module.scss";

type OptionCardProps = {
  icon: ReactNode;
  title: string;
  description: string;
  selected?: boolean;
  onClick?: () => void;
};

export default function OptionCard({
  icon,
  title,
  description,
  selected = false,
  onClick,
}: OptionCardProps) {
  return (
    <button
      type="button"
      className={`${styles.optionCard} ${selected ? styles.selected : ""}`}
      onClick={onClick}
    >
      <span className={styles.icon}>{icon}</span>

      <div>
        <h3 className={styles.title}>{title}</h3>
        <p className={styles.description}>{description}</p>
      </div>
    </button>
  );
}
