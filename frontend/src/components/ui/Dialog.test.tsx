/**
 * Dialog (STU-051): the initial focus goes to the named action whatever characters its ID contains (review U-2 of
 * stu-005-051-review.md). The focus rules of the real dialogs are tested in StudioDialogs.test.tsx.
 */
import { render, screen } from '@testing-library/react';

import { vi } from 'vitest';

import { Dialog } from './Dialog';

const IDS = ['cancel', 'say "no"', "it's fine", 'a]b', 'back\\slash', 'x y', '0-start'];

describe('Dialog initial focus', () => {
  it.each(IDS)('focuses the action with the ID %j', (id) => {
    render(
      <Dialog
        open
        title="Title"
        description="Text"
        actions={[
          { id: 'other', label: 'Other', variant: 'primary', onSelect: () => undefined },
          { id, label: 'Safe', onSelect: () => undefined },
        ]}
        initialFocus={id}
        onCancel={() => undefined}
      />,
    );
    expect(screen.getByRole('button', { name: 'Safe' })).toHaveFocus();
  });
});

describe('Dialog actions', () => {
  it('a disabled action stays focusable, says so (aria-disabled), and does nothing when pressed (TD-043)', () => {
    const onSelect = vi.fn();
    const enabled = vi.fn();
    render(
      <Dialog
        open
        title="Title"
        description="Text"
        actions={[
          { id: 'cancel', label: 'Cancel', onSelect: enabled },
          { id: 'go', label: 'Go', variant: 'primary', onSelect, disabled: true },
        ]}
        initialFocus="cancel"
        onCancel={() => undefined}
      />,
    );
    const go = screen.getByRole('button', { name: 'Go' });
    expect(go).toHaveAttribute('aria-disabled', 'true');
    expect(go).not.toBeDisabled();
    go.click();
    expect(onSelect).not.toHaveBeenCalled();
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(cancel).not.toHaveAttribute('aria-disabled');
    cancel.click();
    expect(enabled).toHaveBeenCalledTimes(1);
  });
});
