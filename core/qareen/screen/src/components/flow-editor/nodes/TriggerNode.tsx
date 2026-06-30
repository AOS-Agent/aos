import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { FlowNode } from '../types';
import BaseNode from './BaseNode';

function TriggerNode(props: NodeProps<FlowNode>) {
  return <BaseNode {...props} inputs={0} outputs={1} />;
}

export default memo(TriggerNode);
