import type { ReactNode } from "react";
import styles from "./PageHeader.module.scss";

type PageHeaderProps = {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
};

export default function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: PageHeaderProps) {
  return (
    <header className={styles.pageHeader}>
      <div className={styles.content}>
        {eyebrow && <p className={styles.eyebrow}>{eyebrow}</p>}

        <h1 className={styles.title}>{title}</h1>

        {description && <p className={styles.description}>{description}</p>}
      </div>

      {action && <div className={styles.action}>{action}</div>}
    </header>
  );
}
