import { ApiError, isRecord, readApiError } from './errors';
import { getCsrfToken, getSessionGeneration, notifySessionExpired } from './session';
import type { SuccessResponse } from './types';

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
  csrf?: boolean;
  redirectOnExpired?: boolean;
};

/** 同源 JSON 客户端：错误转换、CSRF、超时、取消与过期通知。 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers = new Headers({ Accept: 'application/json' });
  const generation = getSessionGeneration();
  const controller = new AbortController();
  let timedOut = false;

  function abortFromCaller(): void {
    controller.abort();
  }

  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener('abort', abortFromCaller, { once: true });

  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey);

  if (method !== 'GET' && options.csrf !== false) {
    const token = getCsrfToken();
    if (!token) {
      options.signal?.removeEventListener('abort', abortFromCaller);
      throw new ApiError(0, 'CSRF_MISSING');
    }
    headers.set('X-CSRF-Token', token);
  }

  const timeoutId = window.setTimeout(function abortOnTimeout() {
    timedOut = true;
    controller.abort();
  }, 15000);

  try {
    const response = await fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });

    let body: unknown = null;
    if (response.status !== 204) {
      const text = await response.text();
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        body = null;
      }
    }

    if (generation !== getSessionGeneration()) {
      throw new DOMException('旧会话请求已取消', 'AbortError');
    }

    if (!response.ok) {
      const error = readApiError(response, body);
      if (
        error.status === 401 &&
        error.code === 'SESSION_EXPIRED' &&
        options.redirectOnExpired !== false
      ) {
        notifySessionExpired();
      }
      throw error;
    }

    if (response.status === 204) return undefined as T;
    if (!isRecord(body) || !('data' in body) || typeof body.request_id !== 'string') {
      throw new ApiError(response.status, 'INVALID_RESPONSE');
    }

    return (body as SuccessResponse<T>).data;
  } catch (error: unknown) {
    if (generation !== getSessionGeneration()) {
      throw new DOMException('旧会话请求已取消', 'AbortError');
    }
    if (options.signal?.aborted) throw error;
    if (timedOut) throw new ApiError(0, 'REQUEST_TIMEOUT', null, true);
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'NETWORK_ERROR', null, true);
  } finally {
    window.clearTimeout(timeoutId);
    options.signal?.removeEventListener('abort', abortFromCaller);
  }
}
