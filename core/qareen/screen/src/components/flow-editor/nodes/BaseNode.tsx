import { memo, type ReactNode } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { FlowNode } from '../types';

import {
  Clock, Send, Mail, Calendar, Sheet, Globe,
  GitBranch, Code, Edit, Zap, Webhook, CheckSquare,
  Bot, Hand, Workflow,
} from 'lucide-react';

const ICONS: Record<string, typeof Zap> = {
  clock: Clock,
  send: Send,
  mail: Mail,
  calendar: Calendar,
  sheet: Sheet,
  globe: Globe,
  'git-branch': GitBranch,
  code: Code,
  edit: Edit,
  zap: Zap,
  webhook: Webhook,
  'check-square': CheckSquare,
  bot: Bot,
  hand: Hand,
  workflow: Workflow,
};

interface BaseNodeProps extends NodeProps<FlowNode> {
  inputs: number;
  outputs: number;
}

function BaseNode({ data, selected, inputs, outputs }: BaseNodeProps) {
  const color = data.color || '#D9730D';
  const IconComponent = ICONS[(data.icon || '').toLowerCase()] || Zap;

  // Compute handle positions distributed vertically
  const inputHandles: ReactNode[] = [];
  for (let i = 0; i < inputs; i++) {
    const top = inputs === 1 ? '50%' : `${((i + 1) / (inputs + 1)) * 100}%`;
    inputHandles.push(
      <Handle
        key={`input-${i}`}
        id={`input-${i}`}
        type="target"
        position={Position.Left}
        style={{
          top,
          width: 10,
          height: 10,
          borderRadius: '50%',
          background: color,
          border: '2px solid #151210',
        }}
      />
    );
  }

  const outputHandles: ReactNode[] = [];
  for (let i = 0; i < outputs; i++) {
    const top = outputs === 1 ? '50%' : `${((i + 1) / (outputs + 1)) * 100}%`;
    outputHandles.push(
      <Handle
        key={`output-${i}`}
        id={`output-${i}`}
        type="source"
        position={Position.Right}
        style={{
          top,
          width: 10,
          height: 10,
          borderRadius: '50%',
          background: color,
          border: '2px solid #151210',
        }}
      />
    );
  }

  return (
    <div
      style={{
        minWidth: 180,
        borderRadius: 10,
        border: `1px solid ${selected ? `${color}99` : 'rgba(255, 245, 235, 0.06)'}`,
        background: 'rgba(21, 18, 16, 0.90)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        padding: '10px 12px',
        fontFamily: 'Inter, sans-serif',
        position: 'relative',
      }}
    >
      {inputHandles}
      {outputHandles}

      {/* Header row: icon badge + label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div
          style={{
            width: 26,
            height: 26,
            borderRadius: 6,
            background: `${color}33`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <IconComponent size={14} color={color} strokeWidth={2} />
        </div>
        <span
          style={{
            fontSize: 13,
            fontWeight: 560,
            color: '#FFFFFF',
            lineHeight: '18px',
          }}
        >
          {data.label || data.n8nName || 'Node'}
        </span>
      </div>

      {/* Category tag */}
      {data.category && (
        <div
          style={{
            marginTop: 4,
            marginLeft: 34,
            fontSize: 10,
            color: '#6B6560',
            lineHeight: '14px',
          }}
        >
          {data.category}
        </div>
      )}
    </div>
  );
}

export default memo(BaseNode);
