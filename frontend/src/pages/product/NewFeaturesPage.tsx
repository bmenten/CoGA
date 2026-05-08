import React from 'react';
import { useQuery } from '@tanstack/react-query';

import PageState from '../../components/PageState';
import api from '../../lib/api';
import type { ApiGithubReleaseCatalog } from '../../lib/apiTypes';
import { githubIssuesUrl, githubReleasesUrl } from '../../lib/githubLinks';

const releaseDateFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
});

const syncDateFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

const formatReleaseDate = (value: string) => {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : releaseDateFormatter.format(parsed);
};

const formatSyncDate = (value: string | null | undefined) => {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : syncDateFormatter.format(parsed);
};

const visibilityLabel = (value: ApiGithubReleaseCatalog['repo_visibility']) => {
  if (value === 'public') {
    return 'Public repository';
  }
  if (value === 'private') {
    return 'Private repository';
  }
  return 'Repository visibility unknown';
};

const NewFeaturesPage: React.FC = () => {
  const { data, isLoading, error } = useQuery<ApiGithubReleaseCatalog>({
    queryKey: ['product', 'releases'],
    queryFn: async () => {
      const response = await api.get('/product/releases');
      return response.data as ApiGithubReleaseCatalog;
    },
    retry: false,
  });

  if (isLoading) {
    return (
      <PageState
        kicker="Product updates"
        title="Loading release history"
        message="Fetching the latest release notes and feature history from GitHub."
      />
    );
  }

  if (error || !data) {
    return (
      <PageState
        kicker="Product updates"
        title="Could not load release history"
        message="The in-app release feed is currently unavailable. You can still open GitHub directly."
        action={
          <>
            <a
              href={githubReleasesUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="button-secondary hover:no-underline"
            >
              Open GitHub releases
            </a>
            <a
              href={githubIssuesUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="button-grey hover:no-underline"
            >
              Submit issue / request
            </a>
          </>
        }
      />
    );
  }

  const syncTimestamp = formatSyncDate(data.fetched_at);

  return (
    <div className="page-shell space-y-6 release-history-page">
      <section className="surface-card page-top-card release-history-hero">
        <div className="space-y-4">
          <div className="space-y-3">
            <p className="page-kicker">Product updates</p>
            <h1 className="catalog-card-title">New features and release history</h1>
            <p className="catalog-card-copy">
              Browse version history synced from GitHub releases, see when features landed, and jump
              directly to the issue tracker when you want to report a problem or request a feature.
            </p>
          </div>

          <div className="release-history-summary-row">
            <span className="badge-chip">Repository {data.repository}</span>
            <span className="badge-chip">{visibilityLabel(data.repo_visibility)}</span>
            <span className="badge-chip">
              {data.sync_status === 'ok'
                ? `Synced ${data.releases.length} release${data.releases.length === 1 ? '' : 's'}`
                : 'GitHub sync unavailable'}
            </span>
            {syncTimestamp ? <span className="badge-chip">Updated {syncTimestamp}</span> : null}
          </div>

          <div className="compact-toolbar dashboard-toolbar release-history-actions">
            <a
              href={data.releases_url || githubReleasesUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="button-secondary hover:no-underline"
            >
              Open GitHub releases
            </a>
            <a
              href={data.issues_url || githubIssuesUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="button-grey hover:no-underline"
            >
              Submit issue / request
            </a>
          </div>
        </div>
      </section>

      {data.sync_status === 'unavailable' ? (
        <section className="surface-card release-history-callout">
          <div className="space-y-2">
            <p className="page-kicker">Sync status</p>
            <h2 className="section-title">GitHub release sync is not available right now</h2>
            <p className="section-copy">
              {data.sync_error ||
                'This deployment cannot read GitHub release metadata yet. External GitHub links remain available.'}
            </p>
          </div>
        </section>
      ) : null}

      <section className="surface-card space-y-4">
        <div className="page-header">
          <div>
            <p className="page-kicker">Version history</p>
            <h2 className="section-title">Release notes</h2>
          </div>
        </div>

        {data.releases.length === 0 ? (
          <p className="table-empty">
            No GitHub releases are published yet. Create a GitHub release to populate this page.
          </p>
        ) : (
          <div className="release-history-list">
            {data.releases.map((release) => (
              <article key={`${release.version}:${release.url}`} className="release-history-card">
                <div className="release-history-card-header">
                  <div className="space-y-2">
                    <div className="release-history-version-row">
                      <span className="release-history-version">{release.version}</span>
                      {release.prerelease ? <span className="badge-chip">Prerelease</span> : null}
                    </div>
                    <h3 className="release-history-title">
                      {release.name && release.name !== release.version
                        ? release.name
                        : `Release ${release.version}`}
                    </h3>
                  </div>
                  <span className="release-history-date">{formatReleaseDate(release.published_at)}</span>
                </div>

                <p className="release-history-summary">{release.summary}</p>

                <div className="compact-toolbar release-history-card-actions">
                  <a
                    href={release.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="button-secondary hover:no-underline"
                  >
                    Open release on GitHub
                  </a>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};

export default NewFeaturesPage;
