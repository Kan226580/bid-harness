/** 当前 A7 只允许回到项目页，拒绝外部 URL 和登录页循环。 */
export function safeReturnTo(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '/projects';

  try {
    const url = new URL(value, window.location.origin);
    if (url.origin !== window.location.origin || url.pathname !== '/projects') {
      return '/projects';
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return '/projects';
  }
}

export function loginUrl(returnTo: string, expired: boolean): string {
  const params = new URLSearchParams({ returnTo: safeReturnTo(returnTo) });
  if (expired) params.set('reason', 'session_expired');
  return `/login?${params}`;
}
