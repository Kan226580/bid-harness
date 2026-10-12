let csrfToken: string | null = null;
let sessionGeneration = 0;

export const SESSION_EXPIRED_EVENT = 'harness:session-expired';

/** 前端只记住 CSRF Token，永远不读取 bid_session。 */
export function setCsrfToken(value: string): void {
  csrfToken = value;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

export function getSessionGeneration(): number {
  return sessionGeneration;
}

/** 登录身份变化后，让旧请求结果失效。 */
export function clearSessionMemory(): void {
  csrfToken = null;
  sessionGeneration += 1;
}

export function notifySessionExpired(): void {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}
