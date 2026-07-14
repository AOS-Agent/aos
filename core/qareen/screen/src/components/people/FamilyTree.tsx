import { useMemo, useCallback } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type EdgeProps,
  MarkerType,
  BaseEdge,
  EdgeLabelRenderer,
  getStraightPath,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { GitBranch } from 'lucide-react';
import { useFamilyTree } from '@/hooks/usePeople';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Skeleton } from '@/components/primitives/Skeleton';
import type { FamilyEdge } from '@/lib/types';

// ---------------------------------------------------------------------------
// Relationship edge colors
// ---------------------------------------------------------------------------

const REL_COLORS: Record<string, string> = {
  spouse: '#D9730D',
  parent: '#BF5AF2',
  child: '#BF5AF2',
  sibling: '#30D158',
};

// ---------------------------------------------------------------------------
// Custom labeled edge
// ---------------------------------------------------------------------------

function LabeledEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps) {
  const [edgePath, labelX, labelY] = getStraightPath({
    sourceX, sourceY, targetX, targetY,
  });
  const rel = (data as { relationship?: string })?.relationship || '';
  const color = REL_COLORS[rel.toLowerCase()] || 'rgba(255,245,235,0.15)';

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={{ stroke: color, strokeWidth: 2 }} />
      {rel && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'none',
            }}
            className="text-[10px] text-text-quaternary bg-bg-secondary/80 px-1.5 py-0.5 rounded-xs border border-border/40"
          >
            {rel}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const edgeTypes = { labeled: LabeledEdge };

// ---------------------------------------------------------------------------
// Layout — hierarchical left-to-right
// ---------------------------------------------------------------------------

function buildLayout(familyEdges: FamilyEdge[]): { nodes: Node[]; edges: Edge[] } {
  // Collect unique people
  const people = new Map<string, string>();
  for (const e of familyEdges) {
    people.set(e.source_id, e.source_name);
    people.set(e.target_id, e.target_name);
  }

  // Build adjacency for BFS levels (parent->child direction)
  const children = new Map<string, string[]>();
  const parents = new Set<string>();
  for (const e of familyEdges) {
    const rel = e.relationship.toLowerCase();
    if (rel === 'parent' || rel === 'child') {
      // source is parent of target (or we need to figure direction)
      const parentId = rel === 'parent' ? e.source_id : e.target_id;
      const childId = rel === 'parent' ? e.target_id : e.source_id;
      const kids = children.get(parentId) ?? [];
      kids.push(childId);
      children.set(parentId, kids);
      parents.add(childId);
    }
  }

  // Find root nodes (those who are not children of anyone)
  const allIds = Array.from(people.keys());
  const roots = allIds.filter(id => !parents.has(id));
  if (roots.length === 0 && allIds.length > 0) roots.push(allIds[0]);

  // BFS for layer assignment
  const layers = new Map<string, number>();
  const queue = [...roots];
  for (const r of roots) layers.set(r, 0);

  while (queue.length > 0) {
    const current = queue.shift()!;
    const layer = layers.get(current) ?? 0;
    for (const child of (children.get(current) ?? [])) {
      if (!layers.has(child)) {
        layers.set(child, layer + 1);
        queue.push(child);
      }
    }
  }

  // Assign remaining (spouse/sibling links) to layer 0 or neighbor layer
  for (const id of allIds) {
    if (!layers.has(id)) {
      // Find a neighbor with a layer
      const neighbor = familyEdges.find(e => e.source_id === id || e.target_id === id);
      if (neighbor) {
        const otherId = neighbor.source_id === id ? neighbor.target_id : neighbor.source_id;
        layers.set(id, layers.get(otherId) ?? 0);
      } else {
        layers.set(id, 0);
      }
    }
  }

  // Group by layer
  const layerGroups = new Map<number, string[]>();
  for (const [id, layer] of layers) {
    const group = layerGroups.get(layer) ?? [];
    group.push(id);
    layerGroups.set(layer, group);
  }

  // Position: top-to-bottom, spread horizontally
  const nodeWidth = 160;
  const nodeHeight = 48;
  const hGap = 40;
  const vGap = 100;

  const rfNodes: Node[] = [];
  const sortedLayers = Array.from(layerGroups.entries()).sort((a, b) => a[0] - b[0]);

  for (const [layer, ids] of sortedLayers) {
    const totalWidth = ids.length * nodeWidth + (ids.length - 1) * hGap;
    const startX = -totalWidth / 2;

    ids.forEach((id, i) => {
      const name = people.get(id) || 'Unknown';
      const initials = name.replace(/[^\p{L}\p{N}\s]/gu, '').trim().split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase();

      rfNodes.push({
        id,
        position: {
          x: startX + i * (nodeWidth + hGap),
          y: layer * (nodeHeight + vGap),
        },
        data: { label: name, initials },
        style: {
          width: nodeWidth,
          height: nodeHeight,
          background: '#1E1A16',
          border: '1px solid rgba(255,245,235,0.1)',
          borderRadius: '7px',
          padding: '8px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          cursor: 'pointer',
          fontSize: '13px',
          fontWeight: 510,
          color: '#E8E4DF',
        },
      });
    });
  }

  const rfEdges: Edge[] = familyEdges.map((e, i) => ({
    id: `fe-${i}`,
    source: e.source_id,
    target: e.target_id,
    type: 'labeled',
    data: { relationship: e.relationship },
    markerEnd: { type: MarkerType.ArrowClosed, color: 'rgba(255,245,235,0.15)' },
  }));

  return { nodes: rfNodes, edges: rfEdges };
}

// ---------------------------------------------------------------------------
// FamilyTree
// ---------------------------------------------------------------------------

export default function FamilyTree({ onSelect }: { onSelect: (personId: string) => void }) {
  const { data, isLoading } = useFamilyTree();

  const familyEdges = data?.edges ?? [];

  const layout = useMemo(() => {
    if (familyEdges.length === 0) return { nodes: [], edges: [] };
    return buildLayout(familyEdges);
  }, [familyEdges]);

  const [nodes, , onNodesChange] = useNodesState(layout.nodes);
  const [edges, , onEdgesChange] = useEdgesState(layout.edges);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    onSelect(node.id);
  }, [onSelect]);

  if (isLoading) {
    return <Skeleton className="h-[500px] w-full rounded-[7px]" />;
  }

  if (familyEdges.length === 0) {
    return (
      <EmptyState
        icon={<GitBranch />}
        title="No family relationships recorded yet"
        description="Family connections will appear here as they are discovered from your contacts."
      />
    );
  }

  return (
    <div className="w-full h-[560px] rounded-[7px] border border-border overflow-hidden bg-bg-secondary">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.4 }}
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
