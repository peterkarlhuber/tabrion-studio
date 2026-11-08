/**
 * Dialog (STU-051): the initial focus goes to the named action whatever characters its ID contains (review U-2 of
 * stu-005-051-review.md). The focus rules of the real dialogs are tested in StudioDialogs.test.tsx.
 */
import { render, screen } from '@testing-library/react';

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
