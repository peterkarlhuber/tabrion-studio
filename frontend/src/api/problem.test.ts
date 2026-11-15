/**
 * The issues of a problem answer (STU-008, STU-XDE-1, G-S2 6A): fixed key paths or positions and fixed codes only;
 * anything else is dropped before the page shows it.
 */
import { readProblem } from './problem';

const answer = (issues: unknown) =>
  new Response(
    JSON.stringify({ code: 'studio.data_editor_constraint', kind: 'database', issues }),
    {
      status: 400,
    },
  );

describe('readProblem', () => {
  it('keeps a value issue at a cell and a constraint issue at a cell or a row, and drops the rest', async () => {
    const problem = await readProblem(
      answer([
        { pointer: 'changes.2.5', code: 'value.too_large' },
        { pointer: 'changes.2', code: 'constraint.unique' },
        { pointer: 'changes.3.1', code: 'constraint.check' },
        { pointer: 'profile.host', code: 'config.required' },
        { pointer: 'changes.2.material', code: 'constraint.unique' },
        { pointer: 'changes.2', code: 'db.unique' },
        { pointer: 'changes.12345', code: 'constraint.unique' },
      ]),
    );
    expect(problem.issues).toEqual([
      { pointer: 'changes.2.5', code: 'value.too_large' },
      { pointer: 'changes.2', code: 'constraint.unique' },
      { pointer: 'changes.3.1', code: 'constraint.check' },
      { pointer: 'profile.host', code: 'config.required' },
    ]);
  });
});
