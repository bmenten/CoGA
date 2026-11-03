import { screen, within } from '@testing-library/react';

/**
 * The `<section>` a heading named `title` heads: how the genome viewers' sidebars group their
 * sample, track and chromosome checkboxes.
 */
export const sectionHeaded = (title: string): HTMLElement => {
  const element = screen.getByRole('heading', { name: title }).closest('section');
  if (!element) throw new Error(`no ${title} section`);
  return element;
};

/** The label of each checkbox in `container`, in the order they are listed. */
export const checkboxLabels = (container: HTMLElement): Array<string | null | undefined> =>
  within(container)
    .getAllByRole('checkbox')
    .map((box) => box.closest('label')?.textContent);
