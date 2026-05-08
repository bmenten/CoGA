const DEFAULT_GITHUB_REPOSITORY_URL = 'https://github.com/bmenten/coga';

const normalizeUrl = (value: string) => value.trim().replace(/\/+$/, '');

export const githubRepositoryUrl =
  normalizeUrl(import.meta.env.VITE_GITHUB_REPOSITORY_URL || DEFAULT_GITHUB_REPOSITORY_URL);

export const githubReleasesUrl = normalizeUrl(
  import.meta.env.VITE_GITHUB_RELEASES_URL || `${githubRepositoryUrl}/releases`
);

export const githubIssuesUrl = normalizeUrl(
  import.meta.env.VITE_GITHUB_ISSUES_URL || `${githubRepositoryUrl}/issues/new/choose`
);
