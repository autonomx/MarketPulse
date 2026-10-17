import { ApiError } from '../api/client';

interface FeatureSubmitFeedbackProps {
  error: unknown;
  errorPrefix: string;
  isError: boolean;
  isPending: boolean;
  jobId?: string;
  pendingMessage: string;
  successPrefix: string;
}

export function FeatureSubmitFeedback({
  error,
  errorPrefix,
  isError,
  isPending,
  jobId,
  pendingMessage,
  successPrefix,
}: FeatureSubmitFeedbackProps) {
  return (
    <>
      {isPending ? (
        <div className="feature-job-link" role="status" aria-live="polite">
          {pendingMessage}
        </div>
      ) : null}

      {isError ? (
        <div className="platform-empty" role="alert">
          {featureSubmitErrorMessage(errorPrefix, error)}
        </div>
      ) : null}

      {jobId && !isPending && !isError ? (
        <div className="feature-job-link" role="status">
          {successPrefix}: {jobId}
        </div>
      ) : null}
    </>
  );
}

export function FeatureValidationMessage({ message, show }: { message: string; show: boolean }) {
  if (!show) {
    return null;
  }

  return (
    <div className="platform-empty" role="alert">
      {message}
    </div>
  );
}

export function featureSubmitErrorMessage(prefix: string, error: unknown): string {
  if (error instanceof ApiError) {
    return `${prefix} failed with status ${error.status}. Check that the gateway is running and reachable.`;
  }

  if (error instanceof Error && error.message) {
    return `${prefix} failed: ${error.message}`;
  }

  return `${prefix} failed. Check that the gateway is running and reachable.`;
}
