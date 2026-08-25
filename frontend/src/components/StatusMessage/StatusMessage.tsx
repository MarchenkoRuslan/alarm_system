import styles from "./StatusMessage.module.scss";

type StatusMessageVariant = "loading" | "error" | "info";

type StatusMessageProps = {
  variant?: StatusMessageVariant;
  children: string;
};

export default function StatusMessage({
  variant = "info",
  children,
}: StatusMessageProps) {
  return (
    <p className={`${styles.statusMessage} ${styles[variant]}`}>{children}</p>
  );
}
