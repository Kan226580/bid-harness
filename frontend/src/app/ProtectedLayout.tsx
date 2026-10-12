import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Layout, Space, Typography } from 'antd';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { authQueryKey, getMe, logout } from '../api/auth';
import { ApiError } from '../api/errors';
import { clearSessionMemory } from '../api/session';
import { PageLoading } from '../components/PageLoading';
import { RequestError } from '../components/RequestError';
import { loginUrl, safeReturnTo } from '../features/auth/redirect';

export function ProtectedLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const me = useQuery({
    queryKey: authQueryKey,
    queryFn: function loadMe({ signal }) {
      return getMe(signal);
    },
    refetchOnMount: 'always',
  });

  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: function afterLogout() {
      clearSessionMemory();
      void queryClient.cancelQueries();
      queryClient.clear();
      navigate('/login', { replace: true });
    },
  });

  if (me.isPending) return <PageLoading />;

  if (me.isError) {
    if (me.error instanceof ApiError && me.error.code === 'SESSION_EXPIRED') {
      // /me 的未登录结果单独处理，清掉任何遗留内存和缓存。
      return <ExpiredSessionRedirect />;
    }
    return (
      <main className="page-shell">
        <RequestError error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />
      </main>
    );
  }

  const currentPath = `${location.pathname}${location.search}${location.hash}`;
  const returnTo = safeReturnTo(new URLSearchParams(location.search).get('returnTo'));
  if (me.data.user.mustChangePassword && location.pathname !== '/change-password') {
    return (
      <Navigate
        to={`/change-password?returnTo=${encodeURIComponent(safeReturnTo(currentPath))}`}
        replace
      />
    );
  }
  if (!me.data.user.mustChangePassword && location.pathname === '/change-password') {
    return <Navigate to={returnTo} replace />;
  }

  return (
    <Layout className="app-layout">
      <Layout.Header className="app-header">
        <Typography.Title level={4}>工程标书工作台</Typography.Title>
        <Space wrap>
          <span>
            {me.data.organization.name} · {me.data.user.displayName}
          </span>
          <Button loading={logoutMutation.isPending} onClick={() => logoutMutation.mutate()}>
            退出登录
          </Button>
        </Space>
      </Layout.Header>
      <Layout.Content className="page-shell">
        {logoutMutation.isError && <RequestError error={logoutMutation.error} />}
        <Outlet context={me.data} />
      </Layout.Content>
    </Layout>
  );
}

function ExpiredSessionRedirect() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  // 清缓存属于副作用，放在 effect 中，不能在 React 渲染期间修改。
  useEffect(() => {
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    clearSessionMemory();
    void queryClient.cancelQueries();
    queryClient.clear();
    navigate(loginUrl(returnTo, true), { replace: true });
  }, [location.pathname, location.search, location.hash, navigate, queryClient]);

  return <PageLoading />;
}
