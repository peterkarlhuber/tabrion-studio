/** The views of the empty shell (STU-005): start, connections, settings, not found. */
import { Database, FolderOpen } from 'lucide-react';
import type { ReactNode } from 'react';
import { Trans, useTranslation } from 'react-i18next';

import { EmptyState } from '../components/ui/EmptyState';
import { currentLocale, setLocale, SUPPORTED_LOCALES } from '../i18n';
import { navigate } from '../router';
import {
  setDensity,
  setThemePreference,
  useTheme,
  type Density,
  type ThemePreference,
} from '../theme';
import styles from './Views.module.css';

/** Inline code in translations (`<code>tabrion-studio</code>`); rendered as elements, never as HTML. */
export const TRANS_COMPONENTS = { code: <code /> };

/** The lockup that matches the surface: black wordmark on light, white wordmark on dark (no CSS filters). */
export function Lockup({ className }: { className?: string }) {
  const { t } = useTranslation();
  const { theme } = useTheme();
  const src =
    theme === 'dark'
      ? '/branding/tabrion-studio-logo-on-dark.png'
      : '/branding/tabrion-studio-logo.png';
  return <img src={src} alt={t('start.logoAlt')} width={320} height={42} className={className} />;
}

function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={styles.pageHeader}>
      <h1 className={styles.title}>{title}</h1>
      {children}
    </div>
  );
}

export function StartView() {
  const { t } = useTranslation();
  return (
    <div className={styles.page}>
      <div className={styles.hero}>
        <Lockup className={styles.lockup} />
        <h1 className={styles.title}>{t('start.title')}</h1>
        <p className={styles.lead}>{t('start.lead')}</p>
      </div>
      <EmptyState
        icon={<FolderOpen />}
        title={t('start.projectsTitle')}
        description={
          <p>
            <Trans i18nKey="start.projectsBody" components={TRANS_COMPONENTS} />
          </p>
        }
      />
    </div>
  );
}

export function ConnectionsView() {
  const { t } = useTranslation();
  return (
    <div className={styles.page}>
      <PageHeader title={t('connections.title')} />
      <EmptyState
        icon={<Database />}
        title={t('connections.emptyTitle')}
        description={<p>{t('connections.emptyBody')}</p>}
      />
    </div>
  );
}

function Choice<T extends string>({
  name,
  legend,
  value,
  options,
  onChange,
}: {
  name: string;
  legend: string;
  value: T;
  options: { value: T; label: string; lang?: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>{legend}</legend>
      <div className={styles.options}>
        {options.map((option) => (
          <label key={option.value} className={styles.option} lang={option.lang}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function SettingsView() {
  const { t } = useTranslation();
  const { preference, density } = useTheme();
  return (
    <div className={styles.page}>
      <PageHeader title={t('settings.title')}>
        <p className={styles.lead}>{t('settings.saved')}</p>
      </PageHeader>
      <section className={styles.card} aria-labelledby="settings-general">
        <h2 id="settings-general" className={styles.cardTitle}>
          {t('settings.general')}
        </h2>
        <Choice
          name="language"
          legend={t('settings.language')}
          value={currentLocale()}
          options={SUPPORTED_LOCALES.map((locale) => ({
            value: locale,
            label: t(`languages.${locale}`),
            lang: locale,
          }))}
          onChange={(locale) => void setLocale(locale)}
        />
        <Choice<ThemePreference>
          name="theme"
          legend={t('settings.theme')}
          value={preference}
          options={[
            { value: 'system', label: t('settings.themeSystem') },
            { value: 'light', label: t('settings.themeLight') },
            { value: 'dark', label: t('settings.themeDark') },
          ]}
          onChange={setThemePreference}
        />
        <Choice<Density>
          name="density"
          legend={t('settings.density')}
          value={density}
          options={[
            { value: 'compact', label: t('settings.densityCompact') },
            { value: 'normal', label: t('settings.densityNormal') },
          ]}
          onChange={setDensity}
        />
      </section>
    </div>
  );
}

export function NotFoundView() {
  const { t } = useTranslation();
  return (
    <div className={styles.page}>
      <PageHeader title={t('notFound.title')} />
      <p>{t('notFound.body')}</p>
      <p>
        <a
          href="/"
          onClick={(event) => {
            event.preventDefault();
            navigate('/');
          }}
        >
          {t('notFound.home')}
        </a>
      </p>
    </div>
  );
}
