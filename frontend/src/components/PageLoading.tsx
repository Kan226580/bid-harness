import { Spin } from 'antd';

export function PageLoading() {
  return (
    <div className="page-loading" role="status" aria-live="polite">
      <Spin />
      <span>正在加载，请稍候……</span>
    </div>
  );
}
