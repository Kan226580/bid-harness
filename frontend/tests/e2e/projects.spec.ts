import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`缺少测试环境变量：${name}`);
  return value;
}

test('登录、新建、刷新后存在，并归档项目', async ({ page }) => {
  const slug = requireEnv('E2E_ORG_SLUG');
  const email = requireEnv('E2E_ADMIN_EMAIL');
  const password = requireEnv('E2E_ADMIN_PASSWORD');
  const projectName = `A7冒烟-${randomUUID()}`;

  await page.goto('/login');
  await page.getByLabel('组织标识', { exact: true }).fill(slug);
  await page.getByLabel('邮箱', { exact: true }).fill(email);
  await page.getByLabel('密码', { exact: true }).fill(password);

  await page.getByRole('button', { name: /^登\s*录$/ }).click();

  await expect(page).toHaveURL(/\/projects(?:\?|$)/);
  await expect(page.getByRole('heading', { name: '项目列表' })).toBeVisible();

  await page.getByRole('button', { name: '新建项目', exact: true }).click();

  const createDialog = page.getByRole('dialog', { name: '新建项目' });
  await createDialog.getByLabel('项目名称', { exact: true }).fill(projectName);
  await createDialog.getByLabel('工程类型', { exact: true }).fill('市政道路');
  await createDialog.getByRole('button', { name: '创建项目', exact: true }).click();
  await expect(createDialog).not.toBeVisible();

  const row = page.getByRole('row').filter({ hasText: projectName });
  await expect(row).toBeVisible();

  await page.reload();
  await expect(row).toBeVisible();

  await row.getByRole('button', { name: /^归\s*档$/ }).click();

  const archiveDialog = page.getByRole('dialog', { name: '确认归档项目' });
  await archiveDialog.getByRole('button', { name: '确认归档', exact: true }).click();

  await expect(archiveDialog).not.toBeVisible();
  await expect(row).not.toBeVisible();

  const statusSelect = page.getByRole('combobox', { name: '项目状态' });
  await statusSelect.click();

  const dropdown = page.locator('.ant-select-dropdown:visible');
  await expect(dropdown).toBeVisible();
  await dropdown.getByText('已归档', { exact: true }).click();

  await expect(statusSelect).toHaveAttribute('aria-expanded', 'false');
  await expect(row).toBeVisible();
  await expect(row).toContainText('已归档');
  await expect(row).toContainText('只读');
});
