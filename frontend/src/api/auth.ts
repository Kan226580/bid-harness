import { request } from './client';
import { setCsrfToken } from './session';
import type { AuthDto, AuthInfo, User, UserDto } from './types';

export type LoginInput = {
  organizationSlug: string;
  email: string;
  password: string;
};

export type ChangePasswordInput = {
  currentPassword: string;
  newPassword: string;
};

export const authQueryKey = ['auth', 'me'] as const;

export function toUser(data: UserDto): User {
  return {
    id: data.id,
    email: data.email,
    displayName: data.display_name,
    role: data.role,
    isActive: data.is_active,
    mustChangePassword: data.must_change_password,
    lockVersion: data.lock_version,
  };
}

function toAuthInfo(data: AuthDto): AuthInfo {
  setCsrfToken(data.csrf_token);
  return { user: toUser(data.user), organization: data.organization };
}

export async function login(input: LoginInput): Promise<AuthInfo> {
  const data = await request<AuthDto>('/auth/login', {
    method: 'POST',
    csrf: false,
    redirectOnExpired: false,
    body: {
      organization_slug: input.organizationSlug.trim(),
      email: input.email.trim(),
      password: input.password,
    },
  });
  return toAuthInfo(data);
}

export async function getMe(signal?: AbortSignal): Promise<AuthInfo> {
  const data = await request<AuthDto>('/auth/me', {
    signal,
    redirectOnExpired: false,
  });
  return toAuthInfo(data);
}

export async function logout(): Promise<void> {
  await request<void>('/auth/logout', { method: 'POST' });
}

export async function changePassword(input: ChangePasswordInput): Promise<User> {
  const data = await request<{ user: UserDto; csrf_token: string }>('/auth/password', {
    method: 'POST',
    body: {
      current_password: input.currentPassword,
      new_password: input.newPassword,
    },
  });
  setCsrfToken(data.csrf_token);
  return toUser(data.user);
}
