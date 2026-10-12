import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Alert, Button, Modal, Typography } from 'antd';
import { ApiError } from '../../api/errors';
import { archiveProject } from '../../api/projects';
import type { Project } from '../../api/types';
import { RequestError } from '../../components/RequestError';

type ArchiveProjectModalProps = {
  project: Project;
  onClose: () => void;
  onArchived: () => void;
  onRefresh: () => void;
};

export function ArchiveProjectModal({
  project,
  onClose,
  onArchived,
  onRefresh,
}: ArchiveProjectModalProps) {
  const [key] = useState(() => crypto.randomUUID());
  const submitting = useRef(false);
  const [succeeded, setSucceeded] = useState(false);
  const mutation = useMutation({
    mutationFn: function submitArchive() {
      return archiveProject(project, key);
    },
  });
  const conflict = mutation.error instanceof ApiError && mutation.error.code === 'VERSION_CONFLICT';

  async function handleArchive(): Promise<void> {
    if (submitting.current || succeeded || conflict) return;
    submitting.current = true;
    try {
      await mutation.mutateAsync();
      setSucceeded(true);
      onArchived();
    } catch {
      // 保留弹窗、原版本号和 key，不擅自覆盖他人的更新。
    } finally {
      submitting.current = false;
    }
  }

  return (
    <Modal
      open
      title="确认归档项目"
      okText="确认归档"
      cancelText="取消"
      okButtonProps={{ danger: true, disabled: conflict || succeeded }}
      confirmLoading={mutation.isPending}
      cancelButtonProps={{ disabled: mutation.isPending }}
      closable={!mutation.isPending}
      maskClosable={false}
      keyboard={!mutation.isPending}
      onOk={() => void handleArchive()}
      onCancel={onClose}
    >
      <Typography.Paragraph>
        即将归档：<strong>{project.name}</strong>
      </Typography.Paragraph>
      <Typography.Paragraph>
        归档后项目只读，保留历史内容；不能继续编辑、上传、生成或审核。本页不提供恢复操作。
      </Typography.Paragraph>
      {mutation.isError && <RequestError error={mutation.error} />}
      {conflict && <Button onClick={onRefresh}>刷新列表后重新确认</Button>}
      {mutation.isError && !conflict && (
        <Alert
          type="info"
          title="如果是网络错误，请保留当前弹窗，稍后再次确认会使用同一个操作编号。"
        />
      )}
    </Modal>
  );
}
