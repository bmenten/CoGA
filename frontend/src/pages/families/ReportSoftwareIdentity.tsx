import React from 'react';

import { formatSoftwareVersion, type ReportBuild } from '../../lib/appVersion';
import { DEVICE_MANUFACTURER, DEVICE_STATUS } from '../../lib/deviceLabel';

/**
 * The device label on a report footer (TF-15 §1): the build that rendered the report, and
 * what CoGA is. A build that could not be loaded is said as such, never left out (#605).
 * A signed version names its own build separately, so it labels this one as the build that
 * rendered the page.
 */
const ReportSoftwareIdentity: React.FC<{ reportBuild: ReportBuild; label?: string }> = ({
  reportBuild,
  label = 'Software:',
}) => (
  <>
    <p className="report-footer-software">
      <span className="report-footer-label">{label}</span>{' '}
      {reportBuild.failed
        ? 'CoGA — the version could not be loaded'
        : reportBuild.build
          ? formatSoftwareVersion(reportBuild.build.version, reportBuild.build.git_sha)
          : 'CoGA — loading the version…'}
    </p>
    <p className="report-footer-device">
      {DEVICE_STATUS} · Manufacturer: {DEVICE_MANUFACTURER}
    </p>
  </>
);

export default ReportSoftwareIdentity;
