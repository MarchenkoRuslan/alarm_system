import styles from "./CreateAlertForm.module.scss";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createAlert } from "../../api/alerts";
import { getRules } from "../../api/rules";
import type { Rule } from "../../types/rule";
import DeliveryStep from "../DeliveryStep/DeliveryStep";
import FiltersStep from "../FiltersStep/FiltersStep";
import ReviewStep from "../ReviewStep/ReviewStep";
import TriggerStep from "../TriggerStep/TriggerStep";
import Button from "@/components/ui/Button/Button";
import { ALERT_DEFAULTS } from "../../config/alertDefaults";
import { buildFiltersJson } from "../../utils/buildFiltersJson";
import { useTelegramUser } from "@/lib/telegram/useTelegramUser";

const steps = ["Trigger", "Filters", "Delivery", "Review"];

export default function CreateAlertForm() {
  const navigate = useNavigate();
  const telegramUser = useTelegramUser();
  const userId = telegramUser ? String(telegramUser.id) : ALERT_DEFAULTS.userId;

  const [currentStep, setCurrentStep] = useState(0);
  const [rules, setRules] = useState<Rule[]>([]);
  const [selectedRuleId, setSelectedRuleId] = useState("");
  const [side, setSide] = useState("any");
  const [minSize, setMinSize] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  const selectedRule = rules.find((rule) => rule.rule_id === selectedRuleId);

  useEffect(() => {
    async function loadRules() {
      try {
        const data = await getRules();
        setRules(data);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Unknown error";
        setError(message);
      } finally {
        setIsLoading(false);
      }
    }

    loadRules();
  }, []);

  function goBack() {
    if (currentStep === 0) {
      navigate("/");
      return;
    }

    setCurrentStep((prev) => prev - 1);
  }

  function goNext() {
    if (currentStep < steps.length - 1) {
      setCurrentStep((prev) => prev + 1);
    }
  }

  async function handleCreateAlert() {
    if (!selectedRule) return;

    const alertId = `alert-${selectedRule.rule_id}-${crypto.randomUUID()}`;

    setIsSubmitting(true);
    setError("");

    try {
      await createAlert({
        alert_id: alertId,
        alert_type: selectedRule.rule_type,
        channels: ALERT_DEFAULTS.channels,
        cooldown_seconds: ALERT_DEFAULTS.cooldownSeconds,
        enabled: true,
        filters_json: buildFiltersJson(selectedRule.rule_type, minSize),
        rule_id: selectedRule.rule_id,
        rule_version: selectedRule.rule_version,
        user_id: userId,
      });

      navigate("/");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (currentStep === steps.length - 1) {
      handleCreateAlert();
      return;
    }

    goNext();
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <header className={styles.header}>
        <Button
          type="button"
          variant="secondary"
          className={styles.backButton}
          onClick={goBack}
        >
          ←
        </Button>

        <div>
          <p className={styles.eyebrow}>New alert</p>
          <h1 className={styles.title}>Create alert</h1>
        </div>
      </header>

      <section className={styles.progress}>
        {steps.map((step, index) => (
          <div
            className={`${styles.step} ${
              index === currentStep ? styles.active : ""
            }`}
            key={step}
          >
            <span className={styles.number}>{index + 1}</span>
            <p className={styles.name}>{step}</p>
          </div>
        ))}
      </section>

      <section className={styles.card}>
        <p className={styles.cardLabel}>Step {currentStep + 1}</p>

        {currentStep === 0 && (
          <TriggerStep
            rules={rules}
            selectedRuleId={selectedRuleId}
            isLoading={isLoading}
            error={error}
            onSelectRule={setSelectedRuleId}
          />
        )}

        {currentStep === 1 && (
          <FiltersStep
            side={side}
            minSize={minSize}
            onChangeSide={setSide}
            onChangeMinSize={setMinSize}
          />
        )}

        {currentStep === 2 && <DeliveryStep />}

        {currentStep === 3 && (
          <ReviewStep
            selectedRule={selectedRule}
            side={side}
            minSize={minSize}
          />
        )}
      </section>

      <div className={styles.actions}>
        <Button type="button" variant="secondary" fullWidth onClick={goBack}>
          Back
        </Button>

        <Button
          type="submit"
          variant="primary"
          fullWidth
          disabled={(currentStep === 0 && !selectedRuleId) || isSubmitting}
        >
          {isSubmitting
            ? "Creating..."
            : currentStep === steps.length - 1
              ? "Create alert"
              : "Continue"}
        </Button>
      </div>
    </form>
  );
}
