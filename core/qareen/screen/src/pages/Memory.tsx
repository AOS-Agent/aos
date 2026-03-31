import { Brain } from 'lucide-react';
import { EmptyState } from '@/components/primitives';

export default function Memory() {
  return (
    <div>
      <h1 className="type-title text-text mb-6">Memory</h1>
      <EmptyState
        icon={<Brain />}
        title="Memory screen"
        description="Agent memory and context management interface will be built here."
      />
    </div>
  );
}
