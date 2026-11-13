/**
 * The parameter panel (03 section 2 C, 08 section 4.4): one row per `:name` placeholder of the text, with its type
 * (text, number, boolean, null, JSON) and its value. A placeholder without a usable value stops a run, and the run
 * moves the focus to its field (08 section 5). Values stay in this page only.
 */
import { useTranslation } from 'react-i18next';

import connectionStyles from '../connections/Connections.module.css';
import { PARAM_TYPES, type ParamInput, type ParamProblem, type ParamType } from './params';
import styles from './Editor.module.css';

/** The id of a placeholder's value field (the run focuses it when the value is missing). */
export function paramFieldId(prefix: string, name: string): string {
  return `${prefix}-param-${name}`;
}

export function ParamPanel({
  prefix,
  names,
  values,
  problem,
  onChange,
}: {
  prefix: string;
  names: string[];
  values: Record<string, ParamInput>;
  problem: { name: string; problem: ParamProblem } | null;
  onChange: (name: string, input: ParamInput) => void;
}) {
  const { t } = useTranslation();
  if (names.length === 0) return null;
  return (
    <section
      className={styles.params}
      aria-labelledby={`${prefix}-params-title`}
      data-testid="param-panel"
      data-region="parameters"
    >
      <h2 id={`${prefix}-params-title`} className={styles.sectionTitle}>
        {t('editor.params.title')}
      </h2>
      <div className={styles.paramRows}>
        {names.map((name) => {
          const input = values[name];
          const type = input?.type ?? 'text';
          const fieldId = paramFieldId(prefix, name);
          const typeId = `${fieldId}-type`;
          const errorId = `${fieldId}-error`;
          const invalid = problem?.name === name;
          const set = (next: Partial<ParamInput>) =>
            onChange(name, { type, value: input?.value ?? '', ...next });
          return (
            <div key={name} className={styles.paramRow} data-testid="param-row">
              <span className={styles.paramName} id={`${fieldId}-name`}>
                :{name}
              </span>
              <label htmlFor={typeId} className="tb-sr-only">
                {t('editor.params.type', { name })}
              </label>
              <select
                id={typeId}
                className={connectionStyles.input}
                value={type}
                onChange={(event) => {
                  const nextType = event.target.value as ParamType;
                  set({
                    type: nextType,
                    value:
                      nextType === 'boolean'
                        ? 'true'
                        : nextType === 'null'
                          ? ''
                          : (input?.value ?? ''),
                  });
                }}
              >
                {PARAM_TYPES.map((item) => (
                  <option key={item} value={item}>
                    {t(`editor.params.types.${item}`)}
                  </option>
                ))}
              </select>
              {type === 'null' ? (
                <span className={connectionStyles.hint} id={fieldId} tabIndex={-1}>
                  NULL
                </span>
              ) : type === 'boolean' ? (
                <select
                  id={fieldId}
                  className={connectionStyles.input}
                  aria-labelledby={`${fieldId}-name`}
                  data-region-entry=""
                  value={input?.value === 'false' ? 'false' : 'true'}
                  onChange={(event) => set({ value: event.target.value })}
                >
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  id={fieldId}
                  className={connectionStyles.input}
                  aria-labelledby={`${fieldId}-name`}
                  data-region-entry=""
                  aria-invalid={invalid ? true : undefined}
                  aria-describedby={invalid ? errorId : undefined}
                  inputMode={type === 'number' ? 'decimal' : undefined}
                  spellCheck={false}
                  autoComplete="off"
                  value={input?.value ?? ''}
                  placeholder={type === 'json' ? '{"key": "value"}' : undefined}
                  onChange={(event) => set({ value: event.target.value })}
                />
              )}
              {invalid ? (
                <p id={errorId} className={connectionStyles.error} data-testid="param-error">
                  {t(`editor.params.problem.${problem.problem}`, { name })}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
