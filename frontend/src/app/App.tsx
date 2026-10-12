import { Button, Result } from 'antd';
import { Link, Navigate, Route, Routes } from 'react-router-dom';
import { NetworkNotice } from '../components/NetworkNotice';
import { ChangePasswordPage } from '../pages/ChangePasswordPage';
import { LoginPage } from '../pages/LoginPage';
import { ProjectsPage } from '../pages/ProjectsPage';
import { ProtectedLayout } from './ProtectedLayout';
import { SessionEvents } from './SessionEvents';

export function App() {
  return (
    <>
      <NetworkNotice />
      <SessionEvents />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedLayout />}>
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/change-password" element={<ChangePasswordPage />} />
        </Route>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route
          path="*"
          element={
            <Result
              status="404"
              title="页面不存在"
              extra={
                <Link to="/projects">
                  <Button type="primary">返回项目列表</Button>
                </Link>
              }
            />
          }
        />
      </Routes>
    </>
  );
}
