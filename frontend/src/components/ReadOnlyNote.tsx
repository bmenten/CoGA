import React from 'react';
import { READ_ONLY_HINT } from '../lib/familyAccess';

/** Says why a reader cannot use the controls beside it (SEC-1). */
const ReadOnlyNote: React.FC = () => (
  <p className="status-note status-note--info" role="note">
    {READ_ONLY_HINT}
  </p>
);

export default ReadOnlyNote;
