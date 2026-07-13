import { Plug, ArrowUpRight } from 'lucide-react';
import { SettingCard, SettingRow } from './shared';
import type { SettingsSection } from './types';

// Integrations now live at /integrations — this section links there.

function IntegrationsContent() {
  return (
    <SettingCard icon={Plug} title="Integrations">
      <SettingRow
        label="Providers, connectors, and credentials"
        description="Manage AI providers, MCP servers, and API keys"
      />
      <a
        href="/integrations"
        className="inline-flex items-center gap-1.5 mt-2 mb-1 h-8 px-3 rounded-md bg-accent/10 hover:bg-accent/20 text-[12px] font-[510] text-accent transition-colors cursor-pointer"
        style={{ transitionDuration: '80ms' }}
      >
        Open Integrations <ArrowUpRight className="w-3 h-3" />
      </a>
    </SettingCard>
  );
}

export const integrationsSection: SettingsSection = {
  id: 'integrations',
  title: 'Integrations',
  icon: Plug,
  component: IntegrationsContent,
};
