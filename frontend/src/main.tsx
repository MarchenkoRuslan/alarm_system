import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { initTelegram } from "@/lib/telegram/telegram";
import { initSentry } from "@/lib/sentry/initSentry";

initTelegram();
initSentry();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
