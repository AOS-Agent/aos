/**
 * NodeConfigPanel — Edit parameters for the selected node.
 *
 * Shows when a node is clicked. Renders form fields based on
 * the node type's field schema from constants.ts.
 */
import { useCallback, useContext, useRef } from 'react';
import { X, Zap, Clock, Send, Mail, Calendar, Sheet, Globe, GitBranch, Code, Edit, Webhook, Bot, Hand, Workflow, ExternalLink, Circle, AlertCircle } from 'lucide-react';
import { useFlowEditor } from '../hooks/useFlowEditor';
import { getNodeDef, CATEGORY_META } from '../constants';
import { ConnectorContext } from '../ConnectorContext';
import { getNodeConnectionStatus, statusDotColor } from '../../../hooks/useConnectorStatus';
import type { FlowNodeData, FieldSchema } from '../types';

const ICONS: Record<string, typeof Zap> = {
  clock: Clock, send: Send, mail: Mail, calendar: Calendar, sheet: Sheet,
  globe: Globe, 'git-branch': GitBranch, code: Code, edit: Edit, zap: Zap,
  webhook: Webhook, bot: Bot, hand: Hand, workflow: Workflow,
};

function ConfigField({
  field,
  value,
  onChange,
}: {
  field: FieldSchema;
  value: unknown;
  onChange: (val: unknown) => void;
}) {
  const strVal = value != null ? String(value) : '';

  switch (field.type) {
    case 'text':
    case 'cron':
      return (
        <input
          value={strVal}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          className="w-full h-7 px-2 rounded-[5px] bg-bg-tertiary border border-border text-[12px] text-text-secondary placeholder:text-text-quaternary focus:outline-none focus:border-accent/40"
        />
      );
    case 'number':
      return (
        <input
          type="number"
          value={strVal}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-full h-7 px-2 rounded-[5px] bg-bg-tertiary border border-border text-[12px] text-text-secondary focus:outline-none focus:border-accent/40"
        />
      );
    case 'select':
      return (
        <select
          value={strVal}
          onChange={(e) => onChange(e.target.value)}
          className="w-full h-7 px-2 rounded-[5px] bg-bg-tertiary border border-border text-[12px] text-text-secondary cursor-pointer focus:outline-none focus:border-accent/40 appearance-none"
        >
          {field.options?.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      );
    case 'textarea':
      return (
        <textarea
          value={strVal}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          rows={4}
          className="w-full px-2 py-1.5 rounded-[5px] bg-bg-tertiary border border-border text-[12px] text-text-secondary placeholder:text-text-quaternary focus:outline-none focus:border-accent/40 resize-none font-mono"
        />
      );
    case 'toggle':
      return (
        <button
          onClick={() => onChange(!value)}
          className={`w-8 h-5 rounded-full transition-colors cursor-pointer ${
            value ? 'bg-accent' : 'bg-bg-quaternary'
          }`}
        >
          <div className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${
            value ? 'translate-x-3.5' : 'translate-x-0.5'
          }`} />
        </button>
      );
    default:
      return null;
  }
}

/** Resolve a nested key like "rule.interval[0].expression" from an object */
function getNestedValue(obj: Record<string, unknown>, key: string): unknown {
  const parts = key.replace(/\[(\d+)\]/g, '.$1').split('.');
  let current: unknown = obj;
  for (const part of parts) {
    if (current == null || typeof current !== 'object') return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

function ConnectionSection({ n8nType, credentials }: { n8nType: string; credentials?: Record<string, unknown> }) {
  const connectorNodeTypes = useContext(ConnectorContext);
  const status = getNodeConnectionStatus(connectorNodeTypes, n8nType);
  const info = connectorNodeTypes[n8nType];
  const dotColor = statusDotColor(status);

  if (status === 'always') {
    // Show credentials if present, but no connection section needed
    if (!credentials || Object.keys(credentials).length === 0) return null;
    return (
      <div className="mt-4 pt-3 border-t border-border">
        <span className="text-[10px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2">
          Credentials
        </span>
        {Object.entries(credentials).map(([type, ref]) => (
          <div key={type} className="flex items-center gap-2 py-1">
            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: '#30D158' }} />
            <span className="text-[11px] text-text-tertiary">
              {(ref as Record<string, unknown>)?.name as string || type}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="mt-4 pt-3 border-t border-border">
      <span className="text-[10px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2">
        Connection
      </span>
      <div className="flex items-center gap-2 py-1">
        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: dotColor }} />
        <span className="text-[11px] text-text-secondary font-[510]">
          {info?.connector_name || n8nType.split('.').pop()}
        </span>
        <span className="text-[10px] text-text-quaternary ml-auto">
          {status === 'connected' ? 'Connected' : status === 'partial' ? 'Partial' : status === 'broken' ? 'Broken' : 'Not configured'}
        </span>
      </div>
      {(status === 'available' || status === 'broken') && (
        <div className="mt-2 flex items-start gap-1.5 p-2 rounded-[5px] bg-[#FF453A]/8">
          <AlertCircle className="w-3 h-3 text-[#FF453A] mt-0.5 flex-shrink-0" />
          <span className="text-[10px] text-[#FF9F8A] leading-[14px]">
            {status === 'broken'
              ? 'Integration is broken. Check credentials and service health.'
              : 'Integration not configured. Set up this connector before deploying.'}
          </span>
        </div>
      )}
      {credentials && Object.keys(credentials).length > 0 && (
        <div className="mt-2">
          <span className="text-[10px] text-text-quaternary block mb-1">Credentials</span>
          {Object.entries(credentials).map(([type, ref]) => (
            <div key={type} className="flex items-center gap-2 py-0.5">
              <span className="text-[11px] text-text-tertiary">
                {(ref as Record<string, unknown>)?.name as string || type}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function NodeConfigPanel() {
  const { selectedNodeId, nodes, updateNodeData, setSelectedNode, mode } = useFlowEditor();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);
  if (!selectedNode) return null;

  const data = selectedNode.data as FlowNodeData;
  const def = getNodeDef(data.n8nType);
  const fields = def?.fields || [];
  const catMeta = CATEGORY_META[data.category] || { label: data.category, color: '#6B6560' };
  const Icon = ICONS[data.icon] || Zap;

  const handleFieldChange = useCallback((fieldKey: string, value: unknown) => {
    if (mode !== 'edit' || !selectedNodeId) return;

    // Debounce updates
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const params = { ...data.parameters, [fieldKey]: value };
      updateNodeData(selectedNodeId, { parameters: params });
    }, 300);
  }, [mode, selectedNodeId, data.parameters, updateNodeData]);

  return (
    <div className="p-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div
            className="w-7 h-7 rounded-[5px] flex items-center justify-center"
            style={{ backgroundColor: data.color + '20' }}
          >
            <Icon className="w-3.5 h-3.5" style={{ color: data.color }} />
          </div>
          <div>
            <span className="text-[13px] font-[560] text-text block">{data.label}</span>
            <span className="text-[10px]" style={{ color: catMeta.color }}>{catMeta.label}</span>
          </div>
        </div>
        <button
          onClick={() => setSelectedNode(null)}
          className="w-6 h-6 flex items-center justify-center rounded-[3px] text-text-quaternary hover:text-text-tertiary cursor-pointer"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Node name */}
      <div className="mb-4">
        <label className="text-[10px] font-[510] text-text-quaternary block mb-1">Name</label>
        {mode === 'edit' ? (
          <input
            value={data.n8nName}
            onChange={(e) => updateNodeData(selectedNodeId!, { n8nName: e.target.value, label: e.target.value })}
            className="w-full h-7 px-2 rounded-[5px] bg-bg-tertiary border border-border text-[12px] text-text-secondary focus:outline-none focus:border-accent/40"
          />
        ) : (
          <span className="text-[12px] text-text-secondary">{data.n8nName}</span>
        )}
      </div>

      {/* Config fields */}
      {fields.length > 0 && (
        <div className="space-y-3">
          <span className="text-[10px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block">
            Parameters
          </span>
          {fields.map((field) => {
            const value = getNestedValue(data.parameters, field.key) ?? field.defaultValue;
            return (
              <div key={field.key}>
                <label className="text-[10px] font-[510] text-text-quaternary block mb-1">
                  {field.label} {field.required && <span className="text-red-400">*</span>}
                </label>
                {mode === 'edit' ? (
                  <ConfigField field={field} value={value} onChange={(v) => handleFieldChange(field.key, v)} />
                ) : (
                  <span className="text-[12px] text-text-tertiary block bg-bg-tertiary rounded-[5px] px-2 py-1">
                    {value != null ? String(value) : '—'}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Connection & Credentials */}
      <ConnectionSection n8nType={data.n8nType} credentials={data.credentials} />

      {/* Agent dispatch details */}
      {data.n8nType === 'aos.agentDispatch' && data.parameters?.agent_id != null && (
        <div className="mt-4 pt-3 border-t border-border">
          <span className="text-[10px] font-[590] text-text-quaternary uppercase tracking-[0.06em] block mb-2">
            Agent
          </span>
          <div className="flex items-center gap-2 py-1">
            <div className="w-5 h-5 rounded-[3px] bg-[#A855F7]/20 flex items-center justify-center">
              <Bot className="w-3 h-3 text-[#A855F7]" />
            </div>
            <span className="text-[12px] text-text-secondary font-[510]">
              {String(data.parameters.agent_id)}
            </span>
          </div>
          <a
            href="/agents"
            className="flex items-center gap-1 mt-2 text-[10px] text-accent hover:text-accent-hover transition-colors"
          >
            <ExternalLink className="w-3 h-3" />
            Browse Agents
          </a>
        </div>
      )}

      {/* n8n type (debug info) */}
      <div className="mt-4 pt-3 border-t border-border">
        <span className="text-[10px] text-text-quaternary">{data.n8nType}</span>
      </div>
    </div>
  );
}
