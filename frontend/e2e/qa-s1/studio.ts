/**
 * QA-S1 automation: one real `tabrion-studio` per test, on its own copy of the QA kit's demo project
 * ("Beispielprojekt": the profiles "Shop (DEV)" and "Shop (PROD)", the SQLite files with Alembic revisions 0002 and
 * 0003, the scripts in `skripte/`), so a case that changes state (trust, saved scripts, settings, a stop) never
 * reaches another case.
 *
 * Needs two environment variables (the suite is skipped without them, like the other real-server suites):
 *
 * - TABRION_STUDIO_E2E_PYTHON: a Python in which Studio is installed with its built frontend;
 * - QA_S1_DEMO_BUILDER: the kit's `build_demo.py`, which writes the demo project (deterministic, standard library).
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { test as base, expect, type Page } from '@playwright/test';

export const python = process.env.TABRION_STUDIO_E2E_PYTHON;
export const demoBuilder = process.env.QA_S1_DEMO_BUILDER;

/** Real-server runs must never reach the developer's keyring (as in backend.spec.ts). */
const NO_KEYRING_ENV = { ...process.env, PYTHON_KEYRING_BACKEND: 'keyring.backends.null.Keyring' };
/** The sign-in link `--print-url` prints: a fresh random loopback address 127.a.b.c per start. */
const LINK = /do not share\): (http:\/\/127(?:\.\d{1,3}){3}:\d+\/launch#\S+)/;

/** A running Studio for one test: its project folder, state folder and sign-in links. */
export class Studio {
  readonly project: string;
  readonly state: string;
  private proc: ChildProcess | undefined;
  private firstLink: string | undefined;
  private log = '';
  private firstUsed = false;

  constructor(readonly root: string) {
    this.project = join(root, 'beispielprojekt');
    this.state = join(root, 'studio-daten');
  }

  private args(...extra: string[]): string[] {
    return ['-m', 'tabrion_studio', this.project, '--state-dir', this.state, ...extra];
  }

  /** Start Studio for the project, as the kit's "2 - Studio starten" does, but print the link instead of a browser. */
  async start(): Promise<void> {
    this.log = '';
    const proc = spawn(python as string, this.args('--no-browser', '--print-url'), {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: NO_KEYRING_ENV,
    });
    this.proc = proc;
    proc.stderr?.on('data', (chunk: Buffer) => {
      this.log += chunk.toString();
    });
    this.firstLink = await new Promise<string>((resolve, reject) => {
      let out = '';
      const timer = setTimeout(
        () => reject(new Error(`no sign-in link: ${out}${this.log}`)),
        30_000,
      );
      proc.stdout?.on('data', (chunk: Buffer) => {
        out += chunk.toString();
        const match = LINK.exec(out);
        if (match?.[1]) {
          clearTimeout(timer);
          resolve(match[1]);
        }
      });
      proc.once('exit', (code) => {
        clearTimeout(timer);
        reject(new Error(`tabrion-studio exited (${code}) before its link: ${out}${this.log}`));
      });
    });
  }

  /** The origin of the running server (http://127.a.b.c:port). */
  get origin(): string {
    return new URL(this.firstLink as string).origin;
  }

  /**
   * A one-time sign-in link: the start's own link first, then a fresh one from a second start for the same project
   * (what double-clicking "2 - Studio starten" again does while Studio runs).
   */
  link(): string {
    if (this.firstLink !== undefined && !this.firstUsed) {
      this.firstUsed = true;
      return this.firstLink;
    }
    const out = execFileSync(python as string, this.args('--no-browser', '--print-url'), {
      encoding: 'utf-8',
      env: NO_KEYRING_ENV,
    });
    const match = LINK.exec(out);
    if (!match?.[1]) throw new Error(`no sign-in link: ${out}`);
    return match[1];
  }

  /** Stop it the way the kit's "3 - Studio beenden" does (`tabrion-studio --stop <project>`), and wait for the exit. */
  async stopLikeTheKit(): Promise<void> {
    const proc = this.proc;
    const exited =
      proc && proc.exitCode === null && proc.signalCode === null
        ? new Promise<void>((resolve) => proc.once('exit', () => resolve()))
        : Promise.resolve();
    execFileSync(python as string, this.args('--stop'), { encoding: 'utf-8', env: NO_KEYRING_ENV });
    await Promise.race([
      exited,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('Studio did not exit after --stop')), 15_000),
      ),
    ]);
    this.firstLink = undefined;
    this.firstUsed = false;
  }

  /** Stop the server if it still runs (SIGTERM) and wait for the exit. */
  async kill(): Promise<void> {
    const proc = this.proc;
    if (proc && proc.exitCode === null && proc.signalCode === null) {
      const exited = new Promise<void>((resolve) => proc.once('exit', () => resolve()));
      proc.kill('SIGTERM');
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 10_000))]);
    }
  }

  /** A file of the demo project, as bytes (to prove that nothing was written). */
  read(relative: string): Buffer {
    return readFileSync(join(this.project, relative));
  }

  exists(relative: string): boolean {
    return existsSync(join(this.project, relative));
  }
}

/** Build the demo project once per worker (the builder is deterministic) and copy it for every test. */
function template(workerRoot: string): string {
  const target = join(workerRoot, 'beispielprojekt');
  if (!existsSync(target)) {
    execFileSync(python as string, ['-I', demoBuilder as string, target], { encoding: 'utf-8' });
  }
  return target;
}

export const test = base.extend<{ studio: Studio }, { demo: string }>({
  demo: [
    // eslint-disable-next-line no-empty-pattern
    async ({}, provide) => {
      const root = mkdtempSync(join(tmpdir(), 'qa-s1-demo-'));
      await provide(template(root));
      rmSync(root, { recursive: true, force: true });
    },
    { scope: 'worker' },
  ],
  studio: async ({ demo }, provide) => {
    const root = mkdtempSync(join(tmpdir(), 'qa-s1-'));
    const studio = new Studio(root);
    cpSync(demo, studio.project, { recursive: true });
    mkdirSync(studio.state);
    await studio.start();
    await provide(studio);
    await studio.kill();
    rmSync(root, { recursive: true, force: true });
  },
});

/** Call at the top of every spec: without a Studio and the demo builder there is nothing to run against. */
export function skipWithoutStudio(): void {
  test.skip(
    !python || !demoBuilder,
    'set TABRION_STUDIO_E2E_PYTHON and QA_S1_DEMO_BUILDER to run the QA-S1 suite',
  );
}

export { expect };

/** Open a sign-in link in the page, as the browser does that Studio opens; wait for the shell. */
export async function signIn(page: Page, link: string): Promise<void> {
  await page.goto(link);
  await expect(page.getByTestId('backend-status')).toBeVisible();
}

/** S1-02: the first start asks D-01 ("Projektprofilen vertrauen?"); trust the project's profiles. */
export async function trustProfiles(page: Page): Promise<void> {
  const dialog = page.getByRole('dialog', { name: 'Projektprofilen vertrauen?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Vertrauen', exact: true }).click();
  await expect(dialog).toBeHidden();
}

/** Sign in with the start's link and trust the project's profiles: where every case after S1-03 begins. */
export async function startSession(page: Page, studio: Studio): Promise<void> {
  await signIn(page, studio.link());
  await trustProfiles(page);
}

/** Choose the option of a profile select whose label begins with the profile's name ("Shop (DEV)"). */
export async function chooseProfile(page: Page, label: string, profile: string): Promise<void> {
  const select = page.getByLabel(label, { exact: true });
  // The options arrive with the profile list; wait for the one of the profile.
  const option = select.locator('option').filter({ hasText: profile });
  await expect(option).toHaveCount(1);
  await select.selectOption({ label: (await option.textContent()) ?? profile });
}

/** Navigate with the side bar's link (its German name). */
export async function goTo(page: Page, name: string): Promise<void> {
  await page.getByRole('link', { name, exact: true }).click();
  // Move the pointer off the side bar, as a user does: the link's tooltip stays while the pointer rests on it and
  // covers the controls next to the side bar (Playwright checks the click target before it moves the pointer).
  await page.getByRole('main').getByRole('heading', { level: 1 }).first().hover();
}

/** The PROD question D-02 ("Produktionsdatenbank"). */
export const prodDialog = (page: Page) =>
  page.getByRole('dialog', { name: 'Produktionsdatenbank' });

/** Open a profile in the explorer ("Explorer" → "Verbindungsprofil" → "Öffnen"); confirm D-02 for PROD. */
export async function openInExplorer(page: Page, profile: string): Promise<void> {
  await goTo(page, 'Explorer');
  await chooseProfile(page, 'Verbindungsprofil', profile);
  await page.getByRole('button', { name: 'Öffnen', exact: true }).click();
  if (profile.includes('PROD')) {
    await prodDialog(page).getByRole('button', { name: 'Verbinden', exact: true }).click();
  }
  await expect(page.getByTestId('explorer-workspace')).toBeVisible();
}

/** Open the SQL editor on a profile with an empty first tab. */
export async function openEditor(page: Page, profile: string): Promise<void> {
  await goTo(page, 'SQL-Editor');
  await expect(page.locator('.cm-content')).toBeVisible();
  await chooseProfile(page, 'Verbindung', profile);
}

/** Replace the text of the editor's active tab, as typing would. */
export async function replaceText(page: Page, sql: string): Promise<void> {
  await page.locator('.cm-content').click();
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Delete');
  await page.keyboard.insertText(sql);
}

/**
 * Wait until the page has handled an action (two animation frames and a task), so that the absence of a dialog or
 * a result is asserted only once it could have appeared. No fixed wait (as in ux-acceptance-1-0.spec.ts).
 */
export async function settled(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 0))),
      ),
  );
}
