import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Alert, Form, Input, Modal, Select } from 'antd';
import { ApiError } from '../../api/errors';
import { createProject } from '../../api/projects';
import type { CreateProjectInput } from '../../api/projects';
import { RequestError } from '../../components/RequestError';

type CreateProjectModalProps = {
  onClose: () => void;
  onCreated: () => void;
};

type CreateAttempt = {
  fingerprint: string;
  key: string;
};

export function CreateProjectModal({ onClose, onCreated }: CreateProjectModalProps) {
  const [form] = Form.useForm<CreateProjectInput>();
  const submitting = useRef(false);
  const attempt = useRef<CreateAttempt | null>(null);
  const [succeeded, setSucceeded] = useState(false);
  const mutation = useMutation({
    mutationFn: function submitProject(values: CreateProjectInput) {
      const input = {
        ...values,
        name: values.name.trim(),
        constructionType: values.constructionType.trim(),
      };
      const fingerprint = JSON.stringify(input);
      if (!attempt.current || attempt.current.fingerprint !== fingerprint) {
        attempt.current = { fingerprint, key: crypto.randomUUID() };
      }
      return createProject(input, attempt.current.key);
    },
  });

  async function handleSubmit(values: CreateProjectInput): Promise<void> {
    if (submitting.current || succeeded) return;
    submitting.current = true;
    try {
      await mutation.mutateAsync(values);
      setSucceeded(true);
      onCreated();
    } catch {
      // 保留输入和本次 key，用户可以核对后重试。
    } finally {
      submitting.current = false;
    }
  }

  const uncertain =
    mutation.isError &&
    mutation.error instanceof ApiError &&
    (mutation.error.status === 0 || mutation.error.status >= 500);

  return (
    <Modal
      open
      title="新建项目"
      okText="创建项目"
      cancelText="取消"
      confirmLoading={mutation.isPending}
      okButtonProps={{ disabled: succeeded }}
      cancelButtonProps={{ disabled: mutation.isPending }}
      closable={!mutation.isPending}
      maskClosable={false}
      keyboard={!mutation.isPending}
      onCancel={onClose}
      onOk={() => form.submit()}
    >
      {mutation.isError && <RequestError error={mutation.error} />}
      {uncertain && (
        <Alert
          type="warning"
          title="结果尚未确认。请保持内容不变重试；若关闭弹窗，请先刷新列表核对是否已创建。"
        />
      )}
      <Form<CreateProjectInput>
        form={form}
        layout="vertical"
        disabled={mutation.isPending || succeeded}
        initialValues={{ description: '', dataClassification: 'synthetic' }}
        onFinish={handleSubmit}
      >
        <Form.Item
          name="name"
          label="项目名称"
          rules={[
            {
              required: true,
              whitespace: true,
              max: 200,
              message: '请输入项目名称，最多 200 字。',
            },
          ]}
        >
          <Input maxLength={200} />
        </Form.Item>
        <Form.Item
          name="constructionType"
          label="工程类型"
          rules={[
            {
              required: true,
              whitespace: true,
              max: 100,
              message: '请输入工程类型，最多 100 字。',
            },
          ]}
        >
          <Input maxLength={100} placeholder="例如市政道路" />
        </Form.Item>
        <Form.Item
          name="description"
          label="项目描述"
          rules={[{ max: 2000, message: '最多 2000 字。' }]}
        >
          <Input.TextArea rows={4} maxLength={2000} showCount />
        </Form.Item>
        <Form.Item name="dataClassification" label="数据分类" rules={[{ required: true }]}>
          <Select
            options={[
              { value: 'synthetic', label: '模拟数据' },
              { value: 'deidentified', label: '脱敏数据' },
              { value: 'confidential', label: '保密数据' },
            ]}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
