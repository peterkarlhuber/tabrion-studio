import { installTooltipDismiss } from './tooltips';

describe('installTooltipDismiss (WCAG 1.4.13)', () => {
  it('Escape hides the tooltips until the pointer moves or the focus changes', () => {
    const root = document.documentElement;
    const stop = installTooltipDismiss(root);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(root).toHaveAttribute('data-tooltips-hidden');
    window.dispatchEvent(new Event('pointermove'));
    expect(root).not.toHaveAttribute('data-tooltips-hidden');
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    window.dispatchEvent(new FocusEvent('focusin'));
    expect(root).not.toHaveAttribute('data-tooltips-hidden');
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    expect(root).not.toHaveAttribute('data-tooltips-hidden');
    stop();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(root).not.toHaveAttribute('data-tooltips-hidden');
  });
});
