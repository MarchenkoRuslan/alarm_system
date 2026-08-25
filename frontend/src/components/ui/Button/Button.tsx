import type { ButtonHTMLAttributes, PropsWithChildren } from "react";
import styles from "./Button.module.scss";

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

type ButtonProps = PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement>
> & {
  variant?: ButtonVariant;
  fullWidth?: boolean;
};

export default function Button({
  children,
  variant = "primary",
  fullWidth = false,
  className = "",
  ...props
}: ButtonProps) {
  return (
    <button
      className={`
        ${styles.button}
        ${styles[variant]}
        ${fullWidth ? styles.fullWidth : ""}
        ${className}
      `}
      {...props}
    >
      {children}
    </button>
  );
}
