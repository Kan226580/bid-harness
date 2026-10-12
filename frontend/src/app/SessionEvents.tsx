import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { clearSessionMemory, SESSION_EXPIRED_EVENT } from '../api/session';
import { loginUrl } from '../features/auth/redirect';

export function SessionEvents() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    function handleExpired(): void {
      const returnTo = `${location.pathname}${location.search}${location.hash}`;
      clearSessionMemory();
      void queryClient.cancelQueries();
      queryClient.clear();
      navigate(loginUrl(returnTo, true), { replace: true });
    }

    window.addEventListener(SESSION_EXPIRED_EVENT, handleExpired);
    return function cleanup() {
      window.removeEventListener(SESSION_EXPIRED_EVENT, handleExpired);
    };
  }, [location.pathname, location.search, location.hash, navigate, queryClient]);

  return null;
}
