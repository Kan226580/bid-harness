const ERROR_MESSAGES: Record<string, string> = {
  AUTHENTICATION_FAILED: '登录信息不正确或账号暂时不可用，请检查后重试。',
  SESSION_EXPIRED: '登录已过期，请重新登录。',
  CSRF_INVALID: '请求校验失败，请刷新页面后重试。',
  CSRF_MISSING: '会话信息尚未恢复，请刷新页面后重试。',
  FORBIDDEN: '你没有执行此操作的权限。',
  PASSWORD_CHANGE_REQUIRED: '请先修改初始密码，再使用项目功能。',
  RESOURCE_NOT_FOUND: '项目不存在或当前账号无法访问，请刷新列表。',
  VERSION_CONFLICT: '数据已经被其他人修改，请刷新后重试。',
  PROJECT_ARCHIVED: '项目已经归档，当前只能查看。',
  RUNS_ACTIVE: '项目还有未结束的任务，请等待任务结束后再归档。',
  IDEMPOTENCY_CONFLICT: '本次操作内容发生变化，请核对后重新操作。',
  IDEMPOTENCY_IN_PROGRESS: '相同操作正在处理中，请稍后用原操作重试。',
  VALIDATION_ERROR: '填写内容不符合要求，请检查字段长度与必填项。',
  RATE_LIMITED: '操作太频繁，请稍后重试。',
  INTERNAL_ERROR: '服务暂时不可用，请稍后重试。',
  NETWORK_ERROR: '无法连接服务，请检查网络与后端服务后重试。',
  REQUEST_TIMEOUT: '请求等待超时，写操作可能已经成功，请核对后重试。',
  INVALID_RESPONSE: '服务返回的数据格式异常，请稍后重试。',
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;
  readonly retryAfter: string | null;

  constructor(
    status: number,
    code: string,
    requestId: string | null = null,
    retryable = false,
    details: Record<string, unknown> = {},
    retryAfter: string | null = null,
  ) {
    const fallback = status === 429 ? '操作太频繁，请稍后重试。' : '请求未完成，请稍后重试。';
    super(ERROR_MESSAGES[code] ?? fallback);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryable = retryable;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 保留结构化字段，界面文案由前端维护，不直接显示服务器原文。 */
export function readApiError(response: Response, body: unknown): ApiError {
  let code = response.status >= 500 ? 'INTERNAL_ERROR' : 'HTTP_ERROR';
  let requestId = response.headers.get('X-Request-ID');
  let retryable = response.status >= 500;
  let details: Record<string, unknown> = {};

  if (isRecord(body)) {
    if (typeof body.request_id === 'string') requestId = body.request_id;
    if (isRecord(body.error)) {
      if (typeof body.error.code === 'string') code = body.error.code;
      if (typeof body.error.retryable === 'boolean') retryable = body.error.retryable;
      if (isRecord(body.error.details)) details = body.error.details;
    }
  }

  return new ApiError(
    response.status,
    code,
    requestId,
    retryable,
    details,
    response.headers.get('Retry-After'),
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return '操作未完成，请稍后重试。';
}
