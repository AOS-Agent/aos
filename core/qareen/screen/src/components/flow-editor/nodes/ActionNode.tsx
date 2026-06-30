import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { FlowNode } from '../types';
import BaseNode from './BaseNode';

function ActionNode(props: NodeProps<FlowNode>) {
  return <BaseNode {...props} inputs={1} outputs={1} />;
}

export default memo(ActionNode);
