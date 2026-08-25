import type { PropsWithChildren } from "react";
import styles from "./PageShell.module.scss";

export default function PageShell({ children }: PropsWithChildren) {
  return (
    <main className={styles.pageShell}>
      <div className={styles.content}>{children}</div>
    </main>
  );
}
