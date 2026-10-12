import { useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { authQueryKey, login } from '../api/auth';
import type { LoginInput } from '../api/auth';
import { clearSessionMemory } from '../api/session';
import { RequestError } from '../components/RequestError';
import { safeReturnTo } from '../features/auth/redirect';

export function LoginPage() {
  const [form] = Form.useForm<LoginInput>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const submitting = useRef(false);
  const returnTo = safeReturnTo(params.get('returnTo'));
  const mutation = useMutation({ mutationFn: login });

  async function handleSubmit(values: LoginInput): Promise<void> {
    if (submitting.current) return;
    submitting.current = true;
    clearSessionMemory();
    await queryClient.cancelQueries();
    queryClient.clear();

    try {
      const data = await mutation.mutateAsync(values);
      queryClient.setQueryData(authQueryKey, data);
      form.resetFields(['password']);
      const next = data.user.mustChangePassword
        ? `/change-password?returnTo=${encodeURIComponent(returnTo)}`
        : returnTo;
      navigate(next, { replace: true });
    } catch {
      form.resetFields(['password']);
    } finally {
      submitting.current = false;
    }
  }

  return (
    <main className="login-shell">
      <Card className="login-card">
        <Typography.Title level={2}>登录工程标书工作台</Typography.Title>
        <Typography.Paragraph type="secondary">使用所在组织分配的账号登录。</Typography.Paragraph>
        {params.get('reason') === 'session_expired' && (
          <Alert type="warning" showIcon title="登录已过期，请重新登录后继续。" />
        )}
        {mutation.isError && <RequestError error={mutation.error} />}
        <Form<LoginInput>
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          disabled={mutation.isPending}
        >
          <Form.Item
            name="organizationSlug"
            label="组织标识"
            rules={[
              {
                required: true,
                whitespace: true,
                max: 100,
                message: '请输入组织标识，最多 100 字。',
              },
            ]}
          >
            <Input
              placeholder="例如 demo-construction"
              autoComplete="organization"
              maxLength={100}
            />
          </Form.Item>
          <Form.Item
            name="email"
            label="邮箱"
            rules={[
              { required: true, message: '请输入邮箱。' },
              { type: 'email', max: 254, message: '请输入有效邮箱，最多 254 字。' },
            ]}
          >
            <Input type="email" autoComplete="username" maxLength={254} />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, max: 128, message: '请输入密码。' }]}
          >
            <Input.Password autoComplete="current-password" maxLength={128} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={mutation.isPending}>
            {mutation.isPending ? '提交中' : '登录'}
          </Button>
        </Form>
      </Card>
    </main>
  );
}
