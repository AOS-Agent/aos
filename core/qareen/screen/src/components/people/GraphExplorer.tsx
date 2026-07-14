import { useMemo, useCallback } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Network } from 'lucide-react';
import { useRelationshipGraph } from '@/hooks/usePeople';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Skeleton } from '@/components/primitives/Skeleton';

// ---------------------------------------------------------------------------
// Category -> node color
// ---------------------------------------------------------------------------

const CATEGORY_NODE_COLORS: Record<string, string> = {
  family: '#D9730D',
  work: '#0A84FF',
  community: '#BF5AF2',
  friends: '#30D158',
  religious: '#2AC3DE',
};
const DEFAULT_NODE_COLOR = '#6B6560';

function nodeColor(organization?: string): string {
  // Infer category from organization name heuristics
  // In practice, we'd use circle data. Here we just use a consistent fallback.
  if (!organization) return DEFAULT_NODE_COLOR;
  return CATEGORY_NODE_COLORS.work;
}

// ---------------------------------------------------------------------------
// Size by importance
// ---------------------------------------------------------------------------

function nodeSize(importance: number): number {
  if (importance === 1) return 48;
  if (importance === 2) return 40;
  if (importance === 3) return 32;
  return 26;
}

// ---------------------------------------------------------------------------
// Simple force-ish layout — spread nodes in concentric rings by importance
// ---------------------------------------------------------------------------

function layoutNodes(
  graphNodes: { id: string; name: string; importance: number; organization?: string }[],
  graphEdges: { source: string; target: string; type: string; strength: number }[],
): { nodes: Node[]; edges: Edge[] } {
  const cx = 400;
  const cy = 300;

  // Group nodes by importance tier for concentric layout
  const tiers: Record<number, typeof graphNodes> = {};
  for (const n of graphNodes) {
    const tier = n.importance;
    (tiers[tier] ??= []).push(n);
  }

  const rfNodes: Node[] = [];
  const tierRadii = [0, 80, 160, 260, 360];

  for (const [tierStr, members] of Object.entries(tiers)) {
    const tier = parseInt(tierStr, 10);
    const radius = tierRadii[tier] ?? 320;
    const count = members.length;

    members.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(count, 1) - Math.PI / 2;
      // Add small jitter for visual interest
      const jitterX = (Math.random() - 0.5) * 20;
      const jitterY = (Math.random() - 0.5) * 20;
      const x = cx + radius * Math.cos(angle) + jitterX;
      const y = cy + radius * Math.sin(angle) + jitterY;
      const size = nodeSize(n.importance);
      const color = nodeColor(n.organization);
      const initials = n.name.replace(/[^\p{L}\p{N}\s]/gu, '').trim().split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase();

      rfNodes.push({
        id: n.id,
        position: { x, y },
        data: { label: initials, fullName: n.name, importance: n.importance },
        type: 'default',
        style: {
          width: size,
          height: size,
          borderRadius: '50%',
          background: color,
          border: '2px solid rgba(255,245,235,0.1)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: size < 32 ? '9px' : '11px',
          fontWeight: 590,
          color: '#FFF',
          cursor: 'pointer',
        },
      });
    });
  }

  const rfEdges: Edge[] = graphEdges.map((e, i) => ({
    id: `e-${i}`,
    source: e.source,
    target: e.target,
    style: {
      stroke: 'rgba(255,245,235,0.12)',
      strokeWidth: Math.max(1, Math.min(e.strength * 3, 4)),
    },
    animated: false,
  }));

  return { nodes: rfNodes, edges: rfEdges };
}

// ---------------------------------------------------------------------------
// GraphExplorer
// ---------------------------------------------------------------------------

export default function GraphExplorer({ onSelect }: { onSelect: (personId: string) => void }) {
  const { data, isLoading } = useRelationshipGraph();

  const graphNodes = data?.nodes ?? [];
  const graphEdges = data?.edges ?? [];

  const layout = useMemo(() => {
    if (graphNodes.length === 0) return { nodes: [], edges: [] };
    return layoutNodes(graphNodes, graphEdges);
  }, [graphNodes, graphEdges]);

  const [nodes, , onNodesChange] = useNodesState(layout.nodes);
  const [edges, , onEdgesChange] = useEdgesState(layout.edges);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    onSelect(node.id);
  }, [onSelect]);

  if (isLoading) {
    return <Skeleton className="h-[500px] w-full rounded-[7px]" />;
  }

  if (graphNodes.length === 0) {
    return (
      <EmptyState
        icon={<Network />}
        title="No relationship data yet"
        description="As you interact with people, the relationship graph will populate automatically."
      />
    );
  }

  return (
    <div className="w-full h-[560px] rounded-[7px] border border-border overflow-hidden bg-bg-secondary">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        minZoom={0.3}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        panOnDrag
        zoomOnScroll
      >
        <Background color="rgba(255,245,235,0.03)" gap={24} />
        <Controls
          showInteractive={false}
          style={{
            background: '#151210',
            border: '1px solid rgba(255,245,235,0.06)',
            borderRadius: '7px',
          }}
        />
      </ReactFlow>
    </div>
  );
}
