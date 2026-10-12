import { Alert, Button } from 'antd';
import { ApiError, errorMessage } from '../api/errors';

type RequestErrorProps = {
  error: unknown;
  onRetry?: () => void;
  retrying?: boolean;
};

export function RequestError({ error, onRetry, retrying = false }: RequestErrorProps) {
  const requestId = error instanceof ApiError ? error.requestId : null;

  return (
    <div role="alert">
      <Alert
        type="error"
        showIcon
        title={errorMessage(error)}
        description={requestId ? `请求编号：${requestId}，反馈问题时可提供此编号。` : undefined}
        action={
          onRetry ? (
            <Button onClick={onRetry} loading={retrying}>
              重试
            </Button>
          ) : undefined
        }
      />
    </div>
  );
}
