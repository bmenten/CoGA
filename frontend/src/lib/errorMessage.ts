type ValidationDetailItem = {
  msg?: string;
  loc?: Array<string | number>;
};

type ErrorMessageOptions = {
  networkFallback?: string;
};

type ApiErrorLike = {
  response?: { data?: { detail?: unknown } };
  request?: unknown;
  code?: string;
  message?: string;
};

const formatValidationDetailItem = (item: ValidationDetailItem): string | null => {
  const message = typeof item.msg === 'string' ? item.msg : null;
  if (!message) {
    return null;
  }

  if (!Array.isArray(item.loc) || item.loc.length === 0) {
    return message;
  }

  const fieldPath = item.loc
    .filter((segment) => segment !== 'body')
    .map(String)
    .join('.');

  return fieldPath ? `${fieldPath}: ${message}` : message;
};

export const isNetworkTransportError = (error: unknown): boolean => {
  if (!error || typeof error !== 'object') {
    return false;
  }

  const candidate = error as ApiErrorLike;
  if (candidate.response) {
    return false;
  }

  return (
    candidate.request != null ||
    candidate.code === 'ERR_NETWORK' ||
    candidate.message === 'Network Error'
  );
};

export const buildApiUnavailableMessage = (baseUrl?: string): string => {
  const normalized = typeof baseUrl === 'string' ? baseUrl.trim() : '';
  const target = normalized ? ` at ${normalized}` : '';
  return `Unable to reach the API${target}. Check that the backend, Postgres, and ClickHouse services are running.`;
};

export const getErrorMessage = (
  error: unknown,
  fallback = 'Something went wrong',
  options?: ErrorMessageOptions
): string => {
  const detail = (error as ApiErrorLike)?.response?.data?.detail;

  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }

  if (Array.isArray(detail)) {
    const formatted = detail
      .map((item) =>
        item && typeof item === 'object'
          ? formatValidationDetailItem(item as ValidationDetailItem)
          : null
      )
      .filter((message): message is string => Boolean(message));

    if (formatted.length > 0) {
      return formatted.join(', ');
    }
  }

  if (isNetworkTransportError(error)) {
    return options?.networkFallback?.trim() || buildApiUnavailableMessage();
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return fallback;
};
