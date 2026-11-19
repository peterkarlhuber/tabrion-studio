import { act } from '@testing-library/react';

import i18n, { detectLocale, matchLocale, setLocale } from './index';
import de from './locales/de.json';
import en from './locales/en.json';

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const english = flatten(en);
const german = flatten(de);

/** Placeholders ({{name}}) and the inline tags the Trans component maps (<code>…</code>). */
function markers(text: string): string[] {
  return [...text.matchAll(/\{\{\s*(\w+)\s*\}\}|<\/?(\w+)>/g)].map((m) => m[0]).sort();
}

// Strings that are the same in both languages on purpose: names, environment labels and loan words.
const SAME_IN_BOTH = new Set([
  // STU-XDM-4: placeholders only ("({{list}})") and "Schema", the same word in German.
  'databaseManager.roles.attributes',
  'databaseManager.roles.kinds.schema',
  // The SQL keyword NULL (the Data Editor's NULL button, STU-026).
  'dataEditor.null',
  // Placeholders only: "{{row}}: {{issue}}" (G-S2 6A).
  'dataEditor.issueAtRow',
  // Placeholders only: "{{name}} ({{source}}, {{environment}}; {{password}})" (STU-CR-6).
  'profileExport.profileLine',
  // Placeholders only (STU-XDM-5): "{{name}} ({{environment}}):", "{{name}} ({{source}}, {{environment}})",
  // "{{name}} ({{kind}})", "{{name}} ({{environment}})" and "{{target}}; {{password}}".
  'dbmProfiles.pairs.read',
  'dbmProfiles.export.profileLine',
  'dbmProfiles.export.leftOutItem',
  'dbmProfiles.import.profileLine',
  'dbmProfiles.import.details',
  // STU-CR-3: PostgreSQL's template name, "Locale (optional)" and the environment tags DEV, TEST, PROD (STU-CR-2).
  'createDatabase.postgres.templates.template0',
  'createDatabase.postgres.locale',
  'createDatabase.profile.environments.dev',
  'createDatabase.profile.environments.test',
  'createDatabase.profile.environments.prod',
  // Product names and technical terms that German uses as they are (STU-008).
  'connections.engine.sqlite',
  'connections.engine.postgresql',
  'connections.detail.engine',
  'connections.detail.server',
  'connections.detail.tls',
  'connections.wizard.postgresql',
  'connections.wizard.host',
  'connections.wizard.port',
  'connections.wizard.name',
  'dialogs.trustProject.heading',
  'dialogs.trustProject.flowTls',
  // The product name of the migration tool (STU-015).
  'migrations.status.alembic',
  'migrations.levuvo.steps.other',
  // The explorer (STU-010): technical terms German uses as they are.
  'nav.explorer',
  'explorer.title',
  'keyboard.help.explorerGroup',
  'explorer.groups.view',
  'explorer.kind.view',
  'explorer.detail.title',
  'explorer.tabs.constraints',
  'explorer.tabs.sqlite',
  'explorer.columns.name',
  'explorer.indexes.name',
  'explorer.extensions.version',
  'explorer.extensions.schema',
  // STU-023: the ER diagram's schema picker, zoom percentage and key lines (names and arrows only).
  'explorer.er.schema',
  // STU-CR-4: the Schema Designer's technical terms German uses as they are (schema, check, index, plan, SQL, name).
  'schemaDesigner.schema',
  'schemaDesigner.element.check',
  'schemaDesigner.element.index',
  'schemaDesigner.form.constraintName',
  'schemaDesigner.kinds.check',
  'schemaDesigner.previewTitle',
  'schemaDesigner.step.sql',
  // STU-XSD-2: the comparison's schema, policy, fingerprint and name columns German uses as they are, AUTOINCREMENT,
  // and lines of placeholders only (a connection with its environment, a side with a feature marker).
  'schemaCompare.schema',
  'schemaCompare.column.policies',
  'schemaCompare.column.fingerprint',
  'schemaCompare.column.name',
  'schemaCompare.features.autoincrement',
  'schemaCompare.connectionOption',
  'schemaCompare.environment',
  'schemaCompare.featureOf',
  'schemaCompare.featureOfTable',
  'schemaCompare.markerNamed',
  'explorer.er.zoom',
  'explorer.er.list.reference',
  'explorer.er.list.referenced',
  // STU-055: an index's method and state in parentheses, and a size in KiB (placeholders only).
  'vectors.indexes.line',
  'vectors.indexes.size',
  // STU-022: the DDL and policies tabs, technical terms German uses as they are.
  'explorer.tabs.ddl',
  'explorer.tabs.policies',
  'explorer.policies.name',
  'app.name',
  'app.title',
  'app.connectionTitle',
  'nav.start',
  'header.label',
  'header.env.dev',
  'header.env.test',
  'header.env.prod',
  'languages.en',
  'languages.de',
  'start.logoAlt',
  'settings.themeSystem',
  'settings.densityNormal',
  'dialogs.trustProject.hosts',
  // SQL editor (STU-012): type names and words German uses as they are.
  'editor.params.types.text',
  'editor.params.types.null',
  'editor.params.types.json',
  'editor.detail.kind.null',
  'editor.detail.kind.text',
  'editor.detail.kind.json',
  'editor.detail.kind.bytes',
  'editor.result.statement',
  // The data viewer (STU-013): the comparison operators are symbols.
  'viewer.ops.eq',
  'viewer.ops.ne',
  'viewer.ops.lt',
  'viewer.ops.le',
  'viewer.ops.gt',
  'viewer.ops.ge',
]);

describe('translations (de and en)', () => {
  it('have the same keys', () => {
    expect(Object.keys(german).sort()).toEqual(Object.keys(english).sort());
  });

  it('have no empty strings', () => {
    for (const [key, value] of [...Object.entries(english), ...Object.entries(german)]) {
      expect(value.trim(), key).not.toBe('');
    }
  });

  it.each(Object.keys(english))('%s keeps the placeholders and tags', (key) => {
    expect(markers(german[key] as string)).toEqual(markers(english[key] as string));
  });

  it('translate every text that is not a name', () => {
    const untranslated = Object.keys(english).filter(
      (key) => english[key] === german[key] && !SAME_IN_BOTH.has(key),
    );
    expect(untranslated).toEqual([]);
  });

  it('use typographic quotes and ellipses in German, not ASCII stand-ins', () => {
    for (const [key, value] of Object.entries(german)) {
      expect(value.includes('...'), key).toBe(false);
      expect(/"/.test(value), key).toBe(false);
    }
  });
});

describe('locale detection', () => {
  afterEach(async () => {
    window.localStorage.clear();
    await act(async () => {
      await i18n.changeLanguage('en');
    });
  });

  it('maps browser tags to the supported languages', () => {
    expect(matchLocale('de-AT')).toBe('de');
    expect(matchLocale('en_US')).toBe('en');
    expect(matchLocale('fr-FR')).toBeNull();
    expect(detectLocale(['fr-FR', 'de-CH'])).toBe('de');
    expect(detectLocale(['es-ES'])).toBe('en');
  });

  it('prefers the stored choice and sets the document language', async () => {
    await act(async () => {
      await setLocale('de');
    });
    expect(detectLocale(['en-US'])).toBe('de');
    expect(document.documentElement.lang).toBe('de');
    expect(i18n.t('nav.settings')).toBe('Einstellungen');
  });
});
