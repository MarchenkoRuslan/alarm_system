import type { Rule } from "../../types/rule";
import styles from "./TriggerStep.module.scss";
import OptionCard from "@/components/OptionCard/OptionCard";
import StatusMessage from "@/components/StatusMessage/StatusMessage";

type TriggerStepProps = {
  rules: Rule[];
  selectedRuleId: string;
  isLoading: boolean;
  error: string;
  onSelectRule: (ruleId: string) => void;
};

export default function TriggerStep({
  rules,
  selectedRuleId,
  isLoading,
  error,
  onSelectRule,
}: TriggerStepProps) {
  return (
    <>
      <h2>Choose alert trigger</h2>
      <p className={styles.description}>
        Select what market event should trigger this alert.
      </p>

      <div className={styles.optionList}>
        {isLoading && (
          <StatusMessage variant="loading">Loading rules...</StatusMessage>
        )}

        {error && <StatusMessage variant="error">{error}</StatusMessage>}

        {!isLoading &&
          !error &&
          rules.map((rule) => (
            <OptionCard
              key={rule.rule_id}
              icon="⚡"
              title={rule.name}
              description={rule.rule_type.replaceAll("_", " ")}
              selected={selectedRuleId === rule.rule_id}
              onClick={() => onSelectRule(rule.rule_id)}
            />
          ))}
      </div>
    </>
  );
}
