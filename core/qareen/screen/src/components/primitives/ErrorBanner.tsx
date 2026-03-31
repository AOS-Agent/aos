import { AlertTriangle, RefreshCw } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';

export function ErrorBanner({ message }: { message?: string }) {
  const queryClient = useQueryClient();

  return (
    <div className="flex items-center gap-3 bg-red-muted rounded-[7px] px-4 py-3 mb-6">
      <AlertTriangle className="w-4 h-4 text-red shrink-0" />
      <span className="text-[13px] text-red flex-1">
        {message || "Can't reach AOS services. Is the backend running?"}
      </span>
      <button
        type="button"
        onClick={() => queryClient.invalidateQueries()}
        className="text-[11px] font-[510] text-red hover:text-text flex items-center gap-1.5 transition-colors"
        style={{ transitionDuration: 'var(--duration-instant)' }}
      >
        <RefreshCw className="w-3 h-3" />
        Retry
      </button>
    </div>
  );
}
