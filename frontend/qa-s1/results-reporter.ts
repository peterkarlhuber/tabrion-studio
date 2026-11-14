/**
 * Writes the QA-S1 results file: one entry per case id, `{ result, note, screenshot, seconds }`, with `result` one of
 * `pass`, `fail` or `not_automated`. It is a file of its own (the option `outputFile`, see playwright.config.ts),
 * never the checklist the tester fills in, so the automation cannot overwrite a manual
 * result. A failed case's screenshot is copied next to it as `screenshots/<id>.png`.
 */
import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';

import type { FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter';

import { CASES, NOT_AUTOMATED } from './cases';

type Outcome = 'pass' | 'fail' | 'not_automated';

interface Entry {
  result: Outcome;
  automation: 'full' | 'partly' | 'none';
  note: string;
  screenshot: string | null;
  seconds: number | null;
}

const ID = /^(S1-\d{2})\b/;

/** The first lines of a failure, without terminal colors. */
function summary(result: TestResult): string {
  // eslint-disable-next-line no-control-regex
  const plain = (result.error?.message ?? result.status).replace(/\u001b\[[0-9;]*m/g, '');
  return plain.split('\n').slice(0, 6).join('\n').trim();
}

export default class ResultsReporter implements Reporter {
  private readonly file: string;
  private readonly entries = new Map<string, Entry>();

  constructor(options: { outputFile?: string } = {}) {
    this.file = options.outputFile ?? join('qa-s1-results', 'results.json');
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    const id = ID.exec(test.title)?.[1];
    if (id === undefined) return;
    const known = CASES.find((item) => item.id === id);
    const passed = result.status === 'passed';
    let screenshot: string | null = null;
    if (!passed) {
      const shot = result.attachments.find(
        (attachment) => attachment.name === 'screenshot' && attachment.path !== undefined,
      );
      if (shot?.path) {
        const target = join(dirname(this.file), 'screenshots', `${id}.png`);
        mkdirSync(dirname(target), { recursive: true });
        copyFileSync(shot.path, target);
        screenshot = relative(dirname(this.file), target);
      }
    }
    const manual = known?.manual ? `Manual part: ${known.manual}` : '';
    this.entries.set(id, {
      result: passed ? 'pass' : 'fail',
      automation: known?.automation ?? 'full',
      note: passed ? manual : [summary(result), manual].filter(Boolean).join('\n\n'),
      screenshot,
      seconds: Math.round(result.duration / 100) / 10,
    });
  }

  onEnd(_result: FullResult): void {
    const out: Record<string, Entry> = {};
    for (const item of CASES) {
      const entry = this.entries.get(item.id);
      if (entry) out[item.id] = entry;
    }
    // Cases of a newer checklist that cases.ts does not list yet still appear.
    for (const [id, entry] of this.entries) out[id] ??= entry;
    for (const item of NOT_AUTOMATED) {
      out[item.id] = {
        result: 'not_automated',
        automation: 'none',
        note: item.reason,
        screenshot: null,
        seconds: null,
      };
    }
    mkdirSync(dirname(this.file), { recursive: true });
    writeFileSync(this.file, `${JSON.stringify(out, null, 2)}\n`);
  }

  printsToStdio(): boolean {
    return false;
  }
}
