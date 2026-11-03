import { screen, waitFor } from '@testing-library/react';
import { expect } from 'vitest';

/** The project the intake pages' `vi.mock` factories serve as the user's one analyst project. */
export const ACCESSIBLE_PROJECT_ID = '11111111-1111-1111-1111-111111111111';

/** The intake panels' project select: the first control labelled Project. */
export const projectSelect = () => screen.getAllByLabelText(/^project$/i)[0];

/** Waits until the project list has loaded and `projectId` is the selected project. */
export const waitForProject = (projectId = ACCESSIBLE_PROJECT_ID) =>
  waitFor(() => expect(projectSelect()).toHaveValue(projectId));
