import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Empty, Input, Select, Space, Table, Tag, Typography } from 'antd';
import type { TableColumnsType } from 'antd';
import { useOutletContext } from 'react-router-dom';
import { listProjects } from '../api/projects';
import type { ProjectFilters } from '../api/projects';
import type { AuthInfo, Project } from '../api/types';
import { RequestError } from '../components/RequestError';
import { ArchiveProjectModal } from '../features/projects/ArchiveProjectModal';
import { CreateProjectModal } from '../features/projects/CreateProjectModal';

const shanghaiTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

type PagingState = {
  cursor: string | null;
  previous: (string | null)[];
};

export function ProjectsPage() {
  const auth = useOutletContext<AuthInfo>();
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const [searchText, setSearchText] = useState('');
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<ProjectFilters['status']>('active');
  const [paging, setPaging] = useState<PagingState>({ cursor: null, previous: [] });
  const [createOpen, setCreateOpen] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<Project | null>(null);

  const canCreate = auth.user.role === 'admin' || auth.user.role === 'writer';
  const canArchive = auth.user.role === 'admin';
  const filters: ProjectFilters = { q, status, cursor: paging.cursor };
  const projectKey = ['projects', auth.organization.id, auth.user.id] as const;
  const query = useQuery({
    queryKey: [...projectKey, filters],
    queryFn: function loadProjects({ signal }) {
      return listProjects(filters, signal);
    },
  });

  function resetPaging(): void {
    setPaging({ cursor: null, previous: [] });
  }

  function handleSearch(value: string): void {
    setQ(value.trim());
    resetPaging();
  }

  function handleStatusChange(value: ProjectFilters['status']): void {
    setStatus(value);
    resetPaging();
  }

  function goNext(): void {
    const nextCursor = query.data?.nextCursor;
    if (!query.data?.hasMore || !nextCursor || query.isFetching) return;
    setPaging((current) => ({
      cursor: nextCursor,
      previous: [...current.previous, current.cursor],
    }));
  }

  function goPrevious(): void {
    setPaging((current) => {
      if (current.previous.length === 0) return current;
      return {
        cursor: current.previous[current.previous.length - 1],
        previous: current.previous.slice(0, -1),
      };
    });
  }

  function refreshLists(): void {
    resetPaging();
    void queryClient.invalidateQueries({ queryKey: projectKey });
  }

  function handleCreated(): void {
    setCreateOpen(false);
    setSearchText('');
    setQ('');
    setStatus('active');
    refreshLists();
    void message.success('项目已创建。');
  }

  function handleArchived(): void {
    setArchiveTarget(null);
    refreshLists();
    void message.success('项目已归档，可切换“已归档”查看。');
  }

  function refreshAfterConflict(): void {
    setArchiveTarget(null);
    refreshLists();
  }

  const columns: TableColumnsType<Project> = [
    { title: '项目名称', dataIndex: 'name', key: 'name' },
    { title: '工程类型', dataIndex: 'constructionType', key: 'constructionType' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (value: Project['status']) => (
        <Tag color={value === 'active' ? 'blue' : 'default'}>
          {value === 'active' ? '进行中' : '已归档'}
        </Tag>
      ),
    },
    {
      title: '更新时间（上海）',
      dataIndex: 'updatedAt',
      key: 'updatedAt',
      render: (value: string) => shanghaiTime.format(new Date(value)),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, project) => {
        if (project.status === 'archived') return '只读';
        if (!canArchive) return '仅管理员可归档';
        return (
          <Button danger onClick={() => setArchiveTarget(project)}>
            归档
          </Button>
        );
      },
    },
  ];

  return (
    <section>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>项目列表</Typography.Title>
          <Typography.Paragraph type="secondary">
            查看本组织项目，管理项目生命周期。
          </Typography.Paragraph>
        </div>
        {canCreate && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建项目
          </Button>
        )}
      </div>

      {!canCreate && (
        <Alert type="info" title="当前审核者账号可以查看项目；新建项目请联系管理员或编写者。" />
      )}

      <Space className="project-toolbar" wrap>
        <Input.Search
          aria-label="搜索项目名称"
          placeholder="输入项目名称，按回车搜索"
          maxLength={100}
          allowClear
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          onSearch={handleSearch}
          enterButton="搜索"
        />
        <Select<ProjectFilters['status']>
          aria-label="项目状态"
          value={status}
          onChange={handleStatusChange}
          options={[
            { value: 'active', label: '进行中' },
            { value: 'archived', label: '已归档' },
            { value: 'all', label: '全部状态' },
          ]}
        />
        <Button onClick={refreshLists} loading={query.isFetching}>
          刷新列表
        </Button>
      </Space>

      {query.isError && (
        <RequestError
          error={query.error}
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
        />
      )}

      <Table<Project>
        rowKey="id"
        columns={columns}
        dataSource={query.data?.items ?? []}
        loading={query.isFetching}
        pagination={false}
        scroll={{ x: 760 }}
        locale={{
          emptyText: query.isError ? (
            '列表未能加载，请点击上方重试。'
          ) : query.isPending ? (
            '正在加载项目……'
          ) : (
            <Empty
              description={
                q || status !== 'active'
                  ? '没有符合条件的项目，请调整筛选。'
                  : canCreate
                    ? '还没有项目，点击右上角新建一个。'
                    : '还没有项目，请联系管理员或编写者创建。'
              }
            />
          ),
        }}
      />

      <Space className="pagination-bar" wrap>
        <Button disabled={paging.previous.length === 0 || query.isFetching} onClick={goPrevious}>
          上一页
        </Button>
        <span>第 {paging.previous.length + 1} 页</span>
        <Button
          disabled={
            !query.data?.hasMore || !query.data.nextCursor || query.isFetching || query.isError
          }
          onClick={goNext}
        >
          下一页
        </Button>
      </Space>

      {createOpen && (
        <CreateProjectModal onClose={() => setCreateOpen(false)} onCreated={handleCreated} />
      )}
      {archiveTarget && (
        <ArchiveProjectModal
          project={archiveTarget}
          onClose={() => setArchiveTarget(null)}
          onArchived={handleArchived}
          onRefresh={refreshAfterConflict}
        />
      )}
    </section>
  );
}
