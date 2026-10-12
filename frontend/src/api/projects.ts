import { request } from './client';
import type {
  CursorPage,
  CursorPageDto,
  DataClassification,
  Project,
  ProjectDto,
  ProjectStatus,
} from './types';

export type CreateProjectInput = {
  name: string;
  constructionType: string;
  description: string;
  dataClassification: DataClassification;
};

export type ProjectFilters = {
  q: string;
  status: ProjectStatus | 'all';
  cursor: string | null;
};

function toProject(data: ProjectDto): Project {
  return {
    id: data.id,
    name: data.name,
    constructionType: data.construction_type,
    description: data.description,
    dataClassification: data.data_classification,
    status: data.status,
    currentBaselineId: data.current_baseline_id,
    baselineDirty: data.baseline_dirty,
    lockVersion: data.lock_version,
    createdBy: data.created_by,
    createdAt: data.created_at,
    updatedAt: data.updated_at,
  };
}

export async function listProjects(
  filters: ProjectFilters,
  signal?: AbortSignal,
): Promise<CursorPage<Project>> {
  const params = new URLSearchParams({ limit: '20' });
  if (filters.q) params.set('q', filters.q);
  if (filters.status !== 'all') params.set('status', filters.status);
  if (filters.cursor) params.set('cursor', filters.cursor);

  const data = await request<CursorPageDto<ProjectDto>>(`/projects?${params}`, { signal });
  return {
    items: data.items.map(toProject),
    nextCursor: data.next_cursor,
    hasMore: data.has_more,
  };
}

export async function createProject(
  input: CreateProjectInput,
  idempotencyKey: string,
): Promise<Project> {
  const data = await request<ProjectDto>('/projects', {
    method: 'POST',
    idempotencyKey,
    body: {
      name: input.name.trim(),
      construction_type: input.constructionType.trim(),
      description: input.description,
      data_classification: input.dataClassification,
    },
  });
  return toProject(data);
}

export async function archiveProject(project: Project, idempotencyKey: string): Promise<Project> {
  const data = await request<ProjectDto>(`/projects/${project.id}/archive`, {
    method: 'POST',
    idempotencyKey,
    body: { expected_lock_version: project.lockVersion },
  });
  return toProject(data);
}
