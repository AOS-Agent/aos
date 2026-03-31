import { ContextColumn } from '@/components/companion/ContextColumn';
import { StreamColumn } from '@/components/companion/StreamColumn';
import { QueueColumn } from '@/components/companion/QueueColumn';
import { VoiceIndicator } from '@/components/companion/VoiceIndicator';
import { MobileQueueSheet } from '@/components/companion/MobileQueueSheet';
import { useCompanion } from '@/hooks/useCompanion';
import { useApprovals } from '@/hooks/useApprovals';

export default function Companion() {
  useCompanion();
  const { cards } = useApprovals();

  return (
    <div className="flex h-full -mx-4 sm:-mx-6 md:-mx-8 -my-4 sm:-my-6">
      {/* Context column — left, hidden below xl */}
      <aside className="hidden xl:flex flex-col w-[280px] min-w-[280px] border-r border-border overflow-y-auto bg-bg-panel">
        <ContextColumn />
      </aside>

      {/* Stream column — center, flex */}
      <main className="flex-1 min-w-0 flex flex-col bg-bg">
        <VoiceIndicator />
        <StreamColumn />
      </main>

      {/* Queue column — right, hidden below md */}
      <aside className="hidden md:flex flex-col w-[320px] min-w-[320px] border-l border-border overflow-y-auto bg-bg-panel">
        <QueueColumn />
      </aside>

      {/* Mobile queue — bottom sheet on small screens */}
      <div className="md:hidden">
        <MobileQueueSheet cardCount={cards.length} />
      </div>
    </div>
  );
}
