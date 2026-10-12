import { useSyncExternalStore } from 'react';
import { Alert } from 'antd';

function subscribe(onChange: () => void): () => void {
  window.addEventListener('online', onChange);
  window.addEventListener('offline', onChange);
  return function unsubscribe() {
    window.removeEventListener('online', onChange);
    window.removeEventListener('offline', onChange);
  };
}

function getSnapshot(): boolean {
  return navigator.onLine;
}

export function NetworkNotice() {
  const online = useSyncExternalStore(subscribe, getSnapshot);
  if (online) return null;

  return (
    <Alert
      banner
      type="warning"
      title="网络已断开，当前显示的数据可能不是最新；恢复连接后请重试。"
    />
  );
}
