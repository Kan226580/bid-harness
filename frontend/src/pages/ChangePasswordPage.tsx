import { useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Card, Form, Input, Typography } from 'antd';
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom';
import { authQueryKey, changePassword } from '../api/auth';
import type { ChangePasswordInput } from '../api/auth';
import type { AuthInfo } from '../api/types';
import { RequestError } from '../components/RequestError';
import { safeReturnTo } from '../features/auth/redirect';

type PasswordForm = ChangePasswordInput & { confirmPassword: string };

export function ChangePasswordPage() {
  const auth = useOutletContext<AuthInfo>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<PasswordForm>();
  const submitting = useRef(false);
  const mutation = useMutation({ mutationFn: changePassword });

  async function handleSubmit(values: PasswordForm): Promise<void> {
    if (submitting.current) return;
    submitting.current = true;
    try {
      const user = await mutation.mutateAsync({
        currentPassword: values.currentPassword,
        newPassword: values.newPassword,
      });
      queryClient.setQueryData<AuthInfo>(authQueryKey, { ...auth, user });
      form.resetFields();
      navigate(safeReturnTo(params.get('returnTo')), { replace: true });
    } catch {
      form.resetFields(['currentPassword']);
    } finally {
      submitting.current = false;
    }
  }

  return (
    <Card className="password-card">
      <Typography.Title level={2}>修改初始密码</Typography.Title>
      <Typography.Paragraph>为保护账号，请先设置 12 到 128 位的新密码。</Typography.Paragraph>
      {mutation.isError && <RequestError error={mutation.error} />}
      <Form<PasswordForm>
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
        disabled={mutation.isPending}
      >
        <Form.Item
          name="currentPassword"
          label="当前密码"
          rules={[{ required: true, max: 128, message: '请输入当前密码。' }]}
        >
          <Input.Password autoComplete="current-password" maxLength={128} />
        </Form.Item>
        <Form.Item
          name="newPassword"
          label="新密码"
          rules={[{ required: true, min: 12, max: 128, message: '新密码必须为 12 到 128 位。' }]}
        >
          <Input.Password autoComplete="new-password" maxLength={128} />
        </Form.Item>
        <Form.Item
          name="confirmPassword"
          label="确认新密码"
          dependencies={['newPassword']}
          rules={[
            { required: true, message: '请再次输入新密码。' },
            ({ getFieldValue }) => ({
              validator(_, value: string) {
                if (!value || value === getFieldValue('newPassword')) return Promise.resolve();
                return Promise.reject(new Error('两次输入的新密码不一致。'));
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" maxLength={128} />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={mutation.isPending}>
          确认修改
        </Button>
      </Form>
    </Card>
  );
}
