import styles from "./FiltersStep.module.scss";

type FiltersStepProps = {
  side: string;
  minSize: string;
  onChangeSide: (side: string) => void;
  onChangeMinSize: (minSize: string) => void;
};

export default function FiltersStep({
  side,
  minSize,
  onChangeSide,
  onChangeMinSize,
}: FiltersStepProps) {
  return (
    <>
      <h2>Add filters</h2>
      <p className={styles.description}>
        Refine when this alert should be triggered.
      </p>

      <div className={styles.formGroup}>
        <label htmlFor="side">Side</label>
        <div className={styles.selectWrapper}>
          <select
            id="side"
            className={styles.select}
            value={side}
            onChange={(event) => onChangeSide(event.target.value)}
          >
            <option value="any">Any</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </div>
      </div>

      <div className={styles.formGroup}>
        <label htmlFor="minSize" className={styles.label}>
          Min size
        </label>
        <input
          id="minSize"
          type="number"
          placeholder="e.g. 10000"
          value={minSize}
          className={styles.input}
          onChange={(event) => onChangeMinSize(event.target.value)}
        />
      </div>
    </>
  );
}
