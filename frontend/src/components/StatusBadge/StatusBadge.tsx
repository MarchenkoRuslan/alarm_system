import styles from "./StatusBadge.module.scss";

type StatusBadgeVariant = "success" | "danger" | "info" | "warning";

type StatusBadgeProps = {
  children: string;
  variant: StatusBadgeVariant;
  className?: string;
};

export default function StatusBadge({
  children,
  variant,
  className,
}: StatusBadgeProps) {
  return (
    <span className={`${styles.badge} ${styles[variant]} ${className ?? ""}`}>
      {children}
    </span>
  );
}
