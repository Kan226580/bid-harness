/** 普通成功响应，T 表示本次接口实际返回的数据。 */
export type SuccessResponse<T> = {
  data: T;
  request_id: string;
};

export type CursorPageDto<T> = {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
};

export type CursorPage<T> = {
  items: T[];
  nextCursor: string | null;
  hasMore: boolean;
};

export type UserRole = 'admin' | 'writer' | 'reviewer';

export type UserDto = {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  is_active: boolean;
  must_change_password: boolean;
  lock_version: number;
};

export type User = {
  id: string;
  email: string;
  displayName: string;
  role: UserRole;
  isActive: boolean;
  mustChangePassword: boolean;
  lockVersion: number;
};

export type Organization = {
  id: string;
  slug: string;
  name: string;
};

export type AuthDto = {
  user: UserDto;
  organization: Organization;
  csrf_token: string;
};

export type AuthInfo = {
  user: User;
  organization: Organization;
};

export type ProjectStatus = 'active' | 'archived';
export type DataClassification = 'synthetic' | 'deidentified' | 'confidential';

export type ProjectDto = {
  id: string;
  name: string;
  construction_type: string;
  description: string;
  data_classification: DataClassification;
  status: ProjectStatus;
  current_baseline_id: string | null;
  baseline_dirty: boolean;
  lock_version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type Project = {
  id: string;
  name: string;
  constructionType: string;
  description: string;
  dataClassification: DataClassification;
  status: ProjectStatus;
  currentBaselineId: string | null;
  baselineDirty: boolean;
  lockVersion: number;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
};
