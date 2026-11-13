/// <reference types="node" />
/**
 * STU-023, the acceptance of 09: "150 Tabellen < 2 s Layout". Timed on the layout itself (`timedScene`:
 * `performance.now()` around boxes, dagre and packing), with a dense schema of 150 tables, up to 30 columns each, and
 * 300 keys. `npm test` runs this file alone in one worker after the other tests (project "perf" in vite.config.ts), so
 * no other test shares the CPU while it measures. The CPU time of the same call is checked too: it is what the layout
 * costs and does not grow when the machine is shared. The browser measures the layout again in
 * `e2e/er-diagram.spec.ts`.
 */
import { MAX_DIAGRAM_TABLES, timedScene } from './layout';
import { denseSchema } from './denseSchema';

describe('the layout of 150 tables (09 STU-023 acceptance)', () => {
  it('takes under 2 s of wall and of CPU time, measured on the layout itself', () => {
    const tables = denseSchema(MAX_DIAGRAM_TABLES);
    expect(tables.reduce((sum, table) => sum + table.foreignKeys.length, 0)).toBeGreaterThanOrEqual(
      300,
    );
    const cpu = process.cpuUsage();
    const { scene, ms } = timedScene('public', tables);
    const used = process.cpuUsage(cpu);
    const cpuMs = (used.user + used.system) / 1000;
    expect(scene.boxes).toHaveLength(MAX_DIAGRAM_TABLES);
    expect(scene.lines.length).toBeGreaterThanOrEqual(300);
    expect(ms).toBeGreaterThan(0);
    expect(ms).toBeLessThan(2000);
    expect(cpuMs).toBeGreaterThan(0);
    expect(cpuMs).toBeLessThan(2000);
  });
});
