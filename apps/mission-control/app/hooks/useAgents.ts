'use client';

import { useQuery } from '@tanstack/react-query';
import { readDir, readTextFile } from '@tauri-apps/plugin-fs';
import { homeDir } from '@tauri-apps/api/path';

export interface AgentMeta {
  id: string;
  name: string;
  description: string;
  model: string;
  tools: string;
}

function parseFrontmatter(content: string): Record<string, string> {
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return {};
  const fm: Record<string, string> = {};
  for (const line of match[1].split('\n')) {
    const idx = line.indexOf(':');
    if (idx > 0) {
      fm[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
  }
  return fm;
}

async function fetchAgents(): Promise<AgentMeta[]> {
  try {
    const home = await homeDir();
    const agentsDir = `${home}.claude/agents`;
    const entries = await readDir(agentsDir);
    const agents: AgentMeta[] = [];

    for (const entry of entries) {
      if (!entry.name?.endsWith('.md') || entry.name.startsWith('--')) continue;
      try {
        const content = await readTextFile(`${agentsDir}/${entry.name}`);
        const fm = parseFrontmatter(content);
        agents.push({
          id: entry.name.replace('.md', ''),
          name: fm.name || entry.name.replace('.md', ''),
          description: fm.description || '',
          model: fm.model || 'opus',
          tools: fm.tools || '',
        });
      } catch {
        // Skip unreadable files
      }
    }
    return agents;
  } catch {
    return [];
  }
}

export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
    refetchInterval: 60_000,
  });
}
