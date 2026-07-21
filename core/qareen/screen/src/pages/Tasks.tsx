/**
 * Tasks — the core Work surface.
 *
 * Three views, same data:
 *   - Stream (default): Grouped list by status. Clean, personal, scrollable.
 *   - Board: Kanban columns. Operational overview.
 *   - Today: Due today + overdue. Personal focus.
 *
 * Design language: Qareen native — warm, serif content, minimal chrome.
 * No glass panels (invisible in dark theme). Solid bg-panel for elevated surfaces.
 */

import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { Plus, X, ChevronDown, ChevronRight, Search, User, Calendar, GripVertical, ArrowUpDown, SlidersHorizontal, Settings2 } from 'lucide-react';
import { DndContext, DragOverlay, useDraggable, useDroppable, closestCenter, type DragStartEvent, type DragEndEvent } from '@dnd-kit/core';
import { useWork, type Task } from '@/hooks/useWork';
import { useCreateTask, useUpdateTask } from '@/hooks/useTasks';
import { useTaskKeyboard } from '@/hooks/useTaskKeyboard';
import { useTaskOverlay } from '@/components/tasks/TaskOverlayContext';
import { Tag } from '@/components/primitives/Tag';
import { DatabaseView } from '@/components/tasks/DatabaseView';
import { TaskStatus, TaskPriority } from '@/lib/types';
import { format, isPast, isToday, isTomorrow, differenceInDays } from 'date-fns';

// ═══════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════

const PRI: Record<number, string> = { 1: '#FF453A', 2: '#FFD60A', 3: '#6B6560', 4: '#0A84FF', 5: '#4A4540' };
const STAT_COLOR: Record<string, string> = { todo: '#6B6560', active: '#0A84FF', waiting: '#FFD60A', done: '#30D158', cancelled: '#4A4540' };
const STAT_LABEL: Record<string, string> = { todo: 'Todo', active: 'Active', waiting: 'Waiting', done: 'Done', cancelled: 'Cancelled' };

function formatDue(iso: string): { text: string; overdue: boolean } {
  const d = new Date(iso);
  if (isToday(d)) return { text: 'Today', overdue: false };
  if (isTomorrow(d)) return { text: 'Tomorrow', overdue: false };
  if (isPast(d)) return { text: `${Math.abs(differenceInDays(d, new Date()))}d overdue`, overdue: true };
  const days = differenceInDays(d, new Date());
  return days <= 7 ? { text: `in ${days}d`, overdue: false } : { text: format(d, 'MMM d'), overdue: false };
}

// ═══════════════════════════════════════════════════════════════════════════
// TaskRow — single task line. Checkbox + priority + serif title + metadata
// ═══════════════════════════════════════════════════════════════════════════

function TaskRow({ task, onSelect, isSelected, isFocused }: { task: Task; onSelect: () => void; isSelected?: boolean; isFocused?: boolean }) {
  const update = useUpdateTask();
  const done = task.status === 'done';
  const due = task.due ? formatDue(task.due) : null;

  return (
    <div data-task-id={task.id}
      onClick={onSelect}
      className={`flex items-center gap-3 h-11 px-3 rounded-lg cursor-pointer transition-colors duration-75 group ${
        isSelected ? 'bg-bg-tertiary' : isFocused ? 'bg-bg-secondary ring-1 ring-accent/20' : 'hover:bg-bg-secondary'
      }`}
    >
      {/* Checkbox */}
      <button
        onClick={e => { e.stopPropagation(); update.mutate({ id: task.id, data: { status: done ? TaskStatus.TODO : TaskStatus.DONE } }); }}
        className="w-[18px] h-[18px] rounded-full border-[1.5px] flex items-center justify-center shrink-0 cursor-pointer transition-all duration-75"
        style={{ borderColor: done ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: done ? '#30D158' : 'transparent' }}
      >
        {done && <svg width="10" height="8" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
      </button>

      {/* Priority */}
      <div className="w-[6px] h-[6px] rounded-full shrink-0" style={{ backgroundColor: PRI[task.priority] ?? PRI[3] }} />

      {/* Title */}
      <span className={`flex-1 min-w-0 text-[16px] truncate ${done ? 'text-text-quaternary line-through' : 'text-text-secondary group-hover:text-text'}`}
>
        {task.title}
      </span>

      {/* Metadata — subtle, shows on hover */}
      <div className="flex items-center gap-3 shrink-0 text-[12px] text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity duration-75">
        {task.project && <span>{task.project}</span>}
        {due && <span className={due.overdue ? 'text-red' : ''}>{due.text}</span>}
        {task.assigned_to && <span className="flex items-center gap-1"><User className="w-[10px] h-[10px]" />{task.assigned_to}</span>}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// StatusGroup — collapsible section
// ═══════════════════════════════════════════════════════════════════════════

function StatusGroup({ status, tasks, onSelect, selectedId, focusedId, defaultOpen = true }: {
  status: string; tasks: Task[]; onSelect: (t: Task) => void; selectedId?: string; focusedId?: string; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (tasks.length === 0) return null;
  const display = status === 'done' ? tasks.slice(-10).reverse() : tasks;
  const more = status === 'done' && tasks.length > 10 ? tasks.length - 10 : 0;

  return (
    <div className="mb-1">
      <button onClick={() => setOpen(!open)}
        className="flex items-center gap-2.5 w-full h-9 px-3 rounded-lg cursor-pointer hover:bg-bg-secondary transition-colors duration-75 text-left">
        {open ? <ChevronDown className="w-3.5 h-3.5 text-text-quaternary" /> : <ChevronRight className="w-3.5 h-3.5 text-text-quaternary" />}
        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: STAT_COLOR[status] ?? STAT_COLOR.todo }} />
        <span className="text-[14px] font-[510] text-text-tertiary">{STAT_LABEL[status] ?? status}</span>
        <span className="text-[13px] font-mono text-text-quaternary">{tasks.length}</span>
      </button>
      {open && (
        <div className="pl-3 ml-3 border-l border-border">
          {display.map(t => <TaskRow key={t.id} task={t} onSelect={() => onSelect(t)} isSelected={selectedId === t.id} isFocused={focusedId === t.id} />)}
          {more > 0 && <p className="text-[13px] text-text-quaternary pl-3 py-2 opacity-50">+{more} completed</p>}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Board — DnD-enabled kanban
// ═══════════════════════════════════════════════════════════════════════════

function DraggableCard({ task, onSelect, isFocused }: { task: Task; onSelect: () => void; isFocused?: boolean }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: task.id });
  const due = task.due ? formatDue(task.due) : null;
  const done = task.status === 'done';

  return (
    <div ref={setNodeRef} data-task-id={task.id}
      style={{ transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined, opacity: isDragging ? 0.3 : 1 }}
      className={`relative ${isFocused ? 'ring-1 ring-accent/30 rounded-lg' : ''}`}>
      <button onClick={onSelect}
        className="w-full text-left rounded-lg p-3 bg-bg-secondary hover:bg-bg-tertiary border border-border-secondary hover:border-border-tertiary cursor-pointer transition-all duration-75 group">
        <div className="flex items-start gap-2">
          {/* Drag handle */}
          <div {...listeners} {...attributes} className="mt-[3px] shrink-0 cursor-grab active:cursor-grabbing opacity-0 group-hover:opacity-40 transition-opacity duration-75">
            <GripVertical className="w-3 h-3 text-text-quaternary" />
          </div>
          <div className="w-[6px] h-[6px] rounded-full mt-[7px] shrink-0" style={{ backgroundColor: PRI[task.priority] }} />
          <span className={`text-[15px] leading-[1.45] line-clamp-2 ${done ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}
    >{task.title}</span>
        </div>
        {(task.project || due) && (
          <div className="flex items-center gap-2 mt-1.5 pl-[22px] text-[12px] text-text-quaternary">
            {task.project && <span>{task.project}</span>}
            {due && <span className={due.overdue ? 'text-red' : ''}>{due.text}</span>}
          </div>
        )}
      </button>
    </div>
  );
}

function DroppableColumn({ status, tasks, onSelect, focusedId }: { status: string; tasks: Task[]; onSelect: (t: Task) => void; focusedId?: string }) {
  const { isOver, setNodeRef } = useDroppable({ id: `column-${status}` });
  const display = status === 'done' ? tasks.slice(-8).reverse() : tasks;
  const more = status === 'done' && tasks.length > 8 ? tasks.length - 8 : 0;
  if (tasks.length === 0 && status !== 'todo') return null;

  return (
    <div ref={setNodeRef} className={`flex-1 min-w-[220px] max-w-[340px] flex flex-col min-h-0 rounded-lg transition-colors duration-75 ${isOver ? 'bg-[rgba(255,245,235,0.03)]' : ''}`}>
      <div className="flex items-center gap-2 px-2 pb-2 shrink-0">
        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: STAT_COLOR[status] }} />
        <span className="text-[13px] font-[510] text-text-tertiary">{STAT_LABEL[status]}</span>
        <span className="text-[12px] font-mono text-text-quaternary">{tasks.length}</span>
      </div>
      <div className="flex-1 overflow-y-auto space-y-1.5 px-1">
        {display.map(t => <DraggableCard key={t.id} task={t} onSelect={() => onSelect(t)} isFocused={focusedId === t.id} />)}
        {more > 0 && <p className="text-[12px] text-text-quaternary text-center py-2 opacity-40">+{more} more</p>}
        {display.length === 0 && <p className="text-[14px] text-text-quaternary text-center py-10 opacity-30">Empty</p>}
      </div>
    </div>
  );
}

function BoardView({ tasks, onSelect, onStatusChange, focusedId }: {
  tasks: Task[]; onSelect: (t: Task) => void; onStatusChange: (taskId: string, newStatus: string) => void; focusedId?: string;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const activeTask = activeId ? tasks.find(t => t.id === activeId) : null;

  const byStatus = useCallback((s: string) => tasks.filter(t => t.status === s), [tasks]);

  const handleDragStart = (event: DragStartEvent) => { setActiveId(event.active.id as string); };
  const handleDragEnd = (event: DragEndEvent) => {
    setActiveId(null);
    const { active, over } = event;
    if (!over) return;
    const overId = over.id as string;
    if (!overId.startsWith('column-')) return;
    const newStatus = overId.replace('column-', '');
    const task = tasks.find(t => t.id === active.id);
    if (task && task.status !== newStatus) {
      onStatusChange(task.id, newStatus);
    }
  };

  return (
    <DndContext onDragStart={handleDragStart} onDragEnd={handleDragEnd} collisionDetection={closestCenter}>
      <div className="flex gap-3 h-full p-2">
        <DroppableColumn status="todo" tasks={byStatus('todo')} onSelect={onSelect} focusedId={focusedId} />
        <DroppableColumn status="active" tasks={byStatus('active')} onSelect={onSelect} focusedId={focusedId} />
        <DroppableColumn status="waiting" tasks={byStatus('waiting')} onSelect={onSelect} focusedId={focusedId} />
        <DroppableColumn status="done" tasks={byStatus('done')} onSelect={onSelect} focusedId={focusedId} />
      </div>
      <DragOverlay>
        {activeTask && (
          <div className="rounded-lg p-3 bg-bg-tertiary border border-accent/40 shadow-[0_8px_24px_rgba(0,0,0,0.4)] w-[280px] rotate-[2deg]">
            <div className="flex items-start gap-2">
              <div className="w-[6px] h-[6px] rounded-full mt-[7px]" style={{ backgroundColor: PRI[activeTask.priority] }} />
              <span className="text-[15px] text-text-secondary line-clamp-2">{activeTask.title}</span>
            </div>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// TodayView — personal focus
// ═══════════════════════════════════════════════════════════════════════════

function TodayView({ tasks, onSelect }: { tasks: Task[]; onSelect: (t: Task) => void }) {
  const update = useUpdateTask();
  const now = new Date();

  const notDone = (t: Task) => t.status !== 'done' && t.status !== 'cancelled';
  const overdue = tasks.filter(t => t.due && isPast(new Date(t.due)) && !isToday(new Date(t.due)) && notDone(t));
  const today = tasks.filter(t => t.due && isToday(new Date(t.due)) && notDone(t));
  const tomorrow = tasks.filter(t => t.due && isTomorrow(new Date(t.due)) && notDone(t));
  const thisWeek = tasks.filter(t => {
    if (!t.due || !notDone(t)) return false;
    const d = differenceInDays(new Date(t.due), now);
    return d >= 2 && d <= 7;
  });
  const active = tasks.filter(t => t.status === 'active' && !t.due);
  const doneToday = tasks.filter(t => t.status === 'done' && t.completed && isToday(new Date(t.completed)));

  const hasItems = overdue.length + today.length + tomorrow.length + thisWeek.length + active.length > 0;
  const [showDone, setShowDone] = useState(false);

  const Row = ({ task }: { task: Task }) => {
    const done = task.status === 'done';
    const due = task.due ? formatDue(task.due) : null;
    return (
      <div onClick={() => onSelect(task)}
        className="flex items-center gap-3 h-11 px-2 rounded-lg cursor-pointer hover:bg-bg-secondary transition-colors duration-75 group">
        {/* Checkbox */}
        <button onClick={e => { e.stopPropagation(); update.mutate({ id: task.id, data: { status: done ? TaskStatus.TODO : TaskStatus.DONE } }); }}
          className="w-[18px] h-[18px] rounded-full border-[1.5px] flex items-center justify-center shrink-0 cursor-pointer transition-all duration-75"
          style={{ borderColor: done ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: done ? '#30D158' : 'transparent' }}>
          {done && <svg width="10" height="8" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
        </button>
        {/* Priority */}
        <div className="w-[6px] h-[6px] rounded-full shrink-0" style={{ backgroundColor: PRI[task.priority] }} />
        {/* Title */}
        <span className={`flex-1 min-w-0 text-[16px] truncate ${done ? 'text-text-quaternary line-through' : 'text-text-secondary group-hover:text-text'}`}>
          {task.title}
        </span>
        {/* Metadata */}
        <div className="flex items-center gap-2 shrink-0 text-[12px] text-text-quaternary opacity-0 group-hover:opacity-100 transition-opacity duration-75">
          {task.project && <span>{task.project}</span>}
          {due && !done && <span className={due.overdue ? 'text-red' : ''}>{due.text}</span>}
        </div>
      </div>
    );
  };

  const Section = ({ title, items, color, count }: { title: string; items: Task[]; color?: string; count?: number }) => {
    if (items.length === 0) return null;
    return (
      <div className="mb-5">
        <div className="flex items-center gap-2 px-2 mb-1">
          <h3 className={`text-[12px] font-[590] uppercase tracking-[0.06em] ${color ?? 'text-text-quaternary'}`}>{title}</h3>
          <span className="text-[12px] font-mono text-text-quaternary opacity-50">{count ?? items.length}</span>
        </div>
        {items.map(t => <Row key={t.id} task={t} />)}
      </div>
    );
  };

  return (
    <div className="max-w-[540px] mx-auto px-4 py-6">
      <h2 className="text-[26px] font-[600] text-text mb-0.5">Today</h2>
      <p className="text-[15px] text-text-tertiary mb-6">{format(now, 'EEEE, MMMM d')}</p>

      {hasItems ? (
        <>
          <Section title="Overdue" items={overdue} color="text-red" />
          <Section title="Today" items={today} />
          <Section title="Tomorrow" items={tomorrow} />
          <Section title="This week" items={thisWeek} />
          <Section title="In progress" items={active} />
        </>
      ) : (
        <div className="py-16 text-center">
          <p className="text-[17px] text-text-quaternary opacity-50">Nothing on the plate.</p>
          <p className="text-[13px] text-text-quaternary opacity-30 mt-1">Tasks with due dates will appear here.</p>
        </div>
      )}

      {/* Completed today */}
      {doneToday.length > 0 && (
        <div className="mt-4 pt-4 border-t border-border">
          <button onClick={() => setShowDone(!showDone)}
            className="flex items-center gap-2 px-2 cursor-pointer hover:text-text-tertiary transition-colors duration-75">
            {showDone ? <ChevronDown className="w-3 h-3 text-text-quaternary" /> : <ChevronRight className="w-3 h-3 text-text-quaternary" />}
            <span className="text-[12px] font-[590] uppercase tracking-[0.06em] text-green">Completed today</span>
            <span className="text-[12px] font-mono text-text-quaternary opacity-50">{doneToday.length}</span>
          </button>
          {showDone && doneToday.map(t => <Row key={t.id} task={t} />)}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// DetailPanel — right side, solid bg-panel (visible, not invisible glass)
// ═══════════════════════════════════════════════════════════════════════════

function DetailPanel({ task, onClose }: { task: Task; onClose: () => void }) {
  const update = useUpdateTask();
  const [editTitle, setEditTitle] = useState(false);
  const [draft, setDraft] = useState(task.title);
  const due = task.due ? formatDue(task.due) : null;

  useEffect(() => { setDraft(task.title); setEditTitle(false); }, [task.id, task.title]);
  const saveTitle = () => { if (draft.trim() && draft !== task.title) update.mutate({ id: task.id, data: { title: draft.trim() } }); setEditTitle(false); };

  return (
    <div className="h-full bg-bg-panel border-l border-border-secondary overflow-y-auto">
      <div className="p-5">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <span className="text-[12px] font-mono text-text-quaternary">{task.id}</span>
          <button onClick={onClose} className="w-7 h-7 rounded-lg flex items-center justify-center hover:bg-bg-tertiary cursor-pointer transition-colors duration-75">
            <X className="w-4 h-4 text-text-quaternary" />
          </button>
        </div>

        {/* Title */}
        {editTitle ? (
          <input value={draft} onChange={e => setDraft(e.target.value)} onBlur={saveTitle}
            onKeyDown={e => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') { setDraft(task.title); setEditTitle(false); } }}
            autoFocus className="w-full text-[20px] font-[600] text-text bg-transparent outline-none border-b border-accent pb-1 mb-6"
     />
        ) : (
          <h2 onClick={() => setEditTitle(true)}
            className="text-[20px] font-[600] text-text mb-6 cursor-text leading-[1.4] hover:text-accent transition-colors duration-75"
    >{task.title}</h2>
        )}

        {/* Properties */}
        <div className="space-y-3 mb-6">
          <div className="flex items-center justify-between text-[14px]">
            <span className="text-text-quaternary">Status</span>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full" style={{ backgroundColor: STAT_COLOR[task.status] }} />
              <select value={task.status} onChange={e => update.mutate({ id: task.id, data: { status: e.target.value as TaskStatus } })}
                className="bg-transparent text-text-secondary outline-none cursor-pointer text-right appearance-none">
                <option value="todo">Todo</option><option value="active">Active</option><option value="waiting">Waiting</option>
                <option value="done">Done</option><option value="cancelled">Cancelled</option>
              </select>
            </div>
          </div>
          <div className="flex items-center justify-between text-[14px]">
            <span className="text-text-quaternary">Priority</span>
            <div className="flex gap-0.5">{[1,2,3,4,5].map(p =>
              <button key={p} onClick={() => update.mutate({ id: task.id, data: { priority: p as TaskPriority } })}
                className={`w-6 h-6 rounded-md text-[12px] font-[590] cursor-pointer transition-all duration-75 ${
                  task.priority === p ? 'text-text bg-bg-tertiary' : 'text-text-quaternary hover:text-text-tertiary hover:bg-bg-secondary'
                }`}>{p}</button>
            )}</div>
          </div>
          {task.project && <div className="flex justify-between text-[14px]"><span className="text-text-quaternary">Project</span><span className="text-text-secondary">{task.project}</span></div>}
          {task.assigned_to && <div className="flex justify-between text-[14px]"><span className="text-text-quaternary">Assignee</span><span className="text-text-secondary flex items-center gap-1.5"><User className="w-3 h-3" />{task.assigned_to}</span></div>}
          {due && <div className="flex justify-between text-[14px]"><span className="text-text-quaternary">Due</span><span className={`flex items-center gap-1.5 ${due.overdue ? 'text-red' : 'text-text-secondary'}`}><Calendar className="w-3 h-3" />{due.text}</span></div>}
        </div>

        {/* Divider */}
        <div className="border-t border-border mb-5" />

        {/* Tags */}
        {task.tags?.length > 0 && <div className="flex flex-wrap gap-1.5 mb-5">{task.tags.map(t => <Tag key={t} label={t} color="gray" />)}</div>}

        {/* Description — editable */}
        <DescriptionEditor task={task} />

        {/* Subtasks — with add input */}
        <SubtaskSection task={task} />

        {/* Handoff */}
        {task.handoff && (
          <div className="p-3 rounded-lg bg-bg-secondary border border-border mb-5">
            <span className="text-[12px] font-[590] text-purple uppercase tracking-wider">Agent handoff</span>
            <div className="mt-2 space-y-2 text-[14px]">
              {task.handoff.state && <p className="text-text-secondary leading-[1.6]">{task.handoff.state}</p>}
              {task.handoff.next_step && <p className="text-text-tertiary"><span className="text-accent font-[510]">Next</span> {task.handoff.next_step}</p>}
              {task.handoff.blockers?.length > 0 && <p className="text-red/70">Blocked: {task.handoff.blockers.join(', ')}</p>}
            </div>
          </div>
        )}

        {/* Activity / Comments */}
        <div className="mb-5">
          <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Activity</span>
          <div className="mt-2 py-4 text-center">
            <p className="text-[13px] text-text-quaternary opacity-40">Comments and activity will appear here</p>
          </div>
          <div className="flex gap-2 mt-2">
            <input placeholder="Add a comment..." className="flex-1 h-8 px-3 text-[14px] bg-bg-secondary text-text rounded-lg border border-border outline-none placeholder:text-text-quaternary focus:border-border-tertiary" />
            <button className="h-8 px-3 text-[13px] font-[510] text-text-quaternary bg-bg-secondary rounded-lg border border-border hover:bg-bg-tertiary cursor-pointer transition-colors duration-75">Send</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Description Editor ────────────────────────────────────────────────────

function DescriptionEditor({ task }: { task: Task }) {
  const update = useUpdateTask();
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(task.description ?? '');
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setVal(task.description ?? ''); }, [task.description]);
  useEffect(() => { if (editing && ref.current) { ref.current.focus(); ref.current.style.height = 'auto'; ref.current.style.height = ref.current.scrollHeight + 'px'; } }, [editing]);

  const save = () => {
    const trimmed = val.trim();
    if (trimmed !== (task.description ?? '')) update.mutate({ id: task.id, data: { description: trimmed || undefined } as any });
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="mb-5">
        <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Description</span>
        <textarea ref={ref} value={val} onChange={e => { setVal(e.target.value); e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px'; }}
          onBlur={save} onKeyDown={e => { if (e.key === 'Escape') { setVal(task.description ?? ''); setEditing(false); } }}
          className="w-full mt-2 p-2 text-[15px] text-text-secondary bg-bg-secondary rounded-lg border border-border outline-none resize-none leading-[1.6] focus:border-border-tertiary min-h-[60px]"
          placeholder="Add a description..." />
      </div>
    );
  }

  return (
    <div className="mb-5 cursor-text" onClick={() => setEditing(true)}>
      <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Description</span>
      {task.description ? (
        <p className="mt-1 text-[15px] text-text-secondary leading-[1.65] whitespace-pre-wrap hover:bg-bg-secondary rounded-lg p-1 -m-1 transition-colors duration-75">{task.description}</p>
      ) : (
        <p className="mt-1 text-[14px] text-text-quaternary opacity-40 hover:opacity-60 transition-opacity duration-75 p-1 -m-1">Click to add description...</p>
      )}
    </div>
  );
}

// ── Subtask Section ───────────────────────────────────────────────────────

function SubtaskSection({ task }: { task: Task }) {
  const update = useUpdateTask();
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const createTask = useCreateTask();

  const subtasks = task.subtasks ?? [];
  const doneCount = subtasks.filter(s => s.status === 'done').length;

  const addSubtask = () => {
    if (!newTitle.trim()) return;
    createTask.mutate({ title: newTitle.trim(), parent_id: task.id } as any);
    setNewTitle('');
  };

  return (
    <div className="mb-5">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider">Subtasks</span>
        <div className="flex items-center gap-2">
          {subtasks.length > 0 && <span className="text-[12px] font-mono text-text-quaternary">{doneCount}/{subtasks.length}</span>}
          <button onClick={() => setAdding(true)} className="text-[12px] text-accent cursor-pointer hover:text-accent-hover transition-colors duration-75">+ Add</button>
        </div>
      </div>

      {/* Progress bar */}
      {subtasks.length > 0 && (
        <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden mb-2">
          <div className={`h-full rounded-full ${doneCount === subtasks.length ? 'bg-green' : 'bg-accent'}`} style={{ width: `${(doneCount / subtasks.length) * 100}%` }} />
        </div>
      )}

      {/* Subtask list */}
      <div className="space-y-0.5">
        {subtasks.map(sub => (
          <div key={sub.id} className="flex items-center gap-2.5 py-1.5 px-2 rounded-md hover:bg-bg-secondary transition-colors duration-75 group">
            <button onClick={() => update.mutate({ id: sub.id, data: { status: sub.status === 'done' ? TaskStatus.TODO : TaskStatus.DONE } })}
              className="w-[14px] h-[14px] rounded-full border-[1.5px] flex items-center justify-center shrink-0 cursor-pointer"
              style={{ borderColor: sub.status === 'done' ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: sub.status === 'done' ? '#30D158' : 'transparent' }}>
              {sub.status === 'done' && <svg width="7" height="5" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
            </button>
            <span className={`text-[14px] flex-1 ${sub.status === 'done' ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}>{sub.title}</span>
          </div>
        ))}
      </div>

      {/* Add subtask input */}
      {adding && (
        <div className="flex items-center gap-2 mt-1 px-2">
          <div className="w-[14px] h-[14px] rounded-full border-[1.5px] border-accent/30 shrink-0" />
          <input value={newTitle} onChange={e => setNewTitle(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addSubtask(); if (e.key === 'Escape') { setAdding(false); setNewTitle(''); } }}
            autoFocus placeholder="Subtask title..." className="flex-1 text-[14px] bg-transparent text-text outline-none placeholder:text-text-quaternary" />
        </div>
      )}
    </div>
  );
}

// DatabaseView is imported from @/components/tasks/DatabaseView


type SortKey = { field: string; dir: 'asc' | 'desc' };

function _OldDatabaseViewRemoved({ tasks, onSelect, selectedId }: { tasks: Task[]; onSelect: (t: Task) => void; selectedId?: string }) {
  const update = useUpdateTask();
  const [sort, setSort] = useState<SortKey>({ field: 'priority', dir: 'asc' });
  const [groupBy, setGroupBy] = useState<string>('');

  const sorted = useMemo(() => {
    return [...tasks].sort((a, b) => {
      let cmp = 0;
      const f = sort.field;
      if (f === 'title') cmp = a.title.localeCompare(b.title);
      else if (f === 'priority') cmp = a.priority - b.priority;
      else if (f === 'status') cmp = (a.status ?? '').localeCompare(b.status ?? '');
      else if (f === 'project') cmp = (a.project ?? '').localeCompare(b.project ?? '');
      else if (f === 'due') cmp = (a.due ? new Date(a.due).getTime() : Infinity) - (b.due ? new Date(b.due).getTime() : Infinity);
      else if (f === 'assigned_to') cmp = (a.assigned_to ?? '').localeCompare(b.assigned_to ?? '');
      return sort.dir === 'asc' ? cmp : -cmp;
    });
  }, [tasks, sort]);

  const toggleSort = (field: string) => {
    setSort(prev => prev.field === field ? { field, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { field, dir: 'asc' });
  };

  // Group tasks if groupBy is set
  const groups = useMemo(() => {
    if (!groupBy) return [{ label: '', tasks: sorted }];
    const map = new Map<string, Task[]>();
    for (const t of sorted) {
      const key = (groupBy === 'project' ? t.project : groupBy === 'status' ? t.status : groupBy === 'assigned_to' ? t.assigned_to : '') ?? '(none)';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(t);
    }
    return [...map.entries()].map(([label, tasks]) => ({ label, tasks }));
  }, [sorted, groupBy]);

  const columns = [
    { id: 'title', label: 'Task', flex: true },
    { id: 'status', label: 'Status', width: 100 },
    { id: 'priority', label: 'Pri', width: 50 },
    { id: 'project', label: 'Project', width: 90 },
    { id: 'due', label: 'Due', width: 80 },
    { id: 'assigned_to', label: 'Assignee', width: 90 },
  ];

  const HeaderCell = ({ col }: { col: typeof columns[0] }) => (
    <button onClick={() => toggleSort(col.id)}
      className="flex items-center gap-1 text-[12px] font-[590] uppercase tracking-[0.05em] text-text-quaternary hover:text-text-tertiary cursor-pointer transition-colors duration-75 text-left h-full px-2"
      style={col.flex ? { flex: 1, minWidth: 0 } : { width: col.width, flexShrink: 0 }}>
      {col.label}
      {sort.field === col.id && <span className="text-accent text-[10px]">{sort.dir === 'asc' ? '▲' : '▼'}</span>}
    </button>
  );

  const StatusCell = ({ status }: { status: string }) => (
    <div className="flex items-center gap-1.5 px-2" style={{ width: 100, flexShrink: 0 }}>
      <div className="w-[6px] h-[6px] rounded-full" style={{ backgroundColor: STAT_COLOR[status] }} />
      <span className="text-[13px] text-text-tertiary">{STAT_LABEL[status] ?? status}</span>
    </div>
  );

  const PriorityCell = ({ priority }: { priority: number }) => (
    <div className="flex items-center gap-1 px-2" style={{ width: 50, flexShrink: 0 }}>
      <div className="w-[5px] h-[5px] rounded-full" style={{ backgroundColor: PRI[priority] }} />
      <span className="text-[12px] font-[510] text-text-quaternary">P{priority}</span>
    </div>
  );

  const DueCell = ({ due }: { due: string | null }) => {
    if (!due) return <div className="px-2 text-[13px] text-text-quaternary opacity-30" style={{ width: 80, flexShrink: 0 }}>—</div>;
    const d = formatDue(due);
    return <div className={`px-2 text-[13px] ${d.overdue ? 'text-red font-[510]' : 'text-text-quaternary'}`} style={{ width: 80, flexShrink: 0 }}>{d.text}</div>;
  };

  return (
    <div className="h-full flex flex-col">
      {/* Controls row */}
      <div className="flex items-center gap-2 px-3 py-1.5 shrink-0">
        <span className="text-[12px] text-text-quaternary">Group by</span>
        <select value={groupBy} onChange={e => setGroupBy(e.target.value)}
          className="h-6 text-[12px] bg-bg-secondary text-text-tertiary rounded-md px-2 border border-border outline-none cursor-pointer">
          <option value="">None</option>
          <option value="status">Status</option>
          <option value="project">Project</option>
          <option value="assigned_to">Assignee</option>
        </select>
        <span className="text-[12px] text-text-quaternary ml-auto font-mono">{tasks.length} tasks</span>
      </div>

      {/* Header */}
      <div className="flex items-center h-8 border-b border-border-secondary mx-2 shrink-0">
        <div className="w-9 shrink-0" /> {/* checkbox col */}
        {columns.map(col => <HeaderCell key={col.id} col={col} />)}
      </div>

      {/* Rows */}
      <div className="flex-1 overflow-y-auto">
        {groups.map((group, gi) => (
          <div key={gi}>
            {group.label && (
              <div className="flex items-center gap-2 h-8 px-3 bg-bg-secondary/50 border-b border-border sticky top-0 z-10">
                {groupBy === 'status' && <div className="w-2 h-2 rounded-full" style={{ backgroundColor: STAT_COLOR[group.label] }} />}
                <span className="text-[13px] font-[510] text-text-tertiary">{groupBy === 'status' ? (STAT_LABEL[group.label] ?? group.label) : group.label}</span>
                <span className="text-[12px] font-mono text-text-quaternary">{group.tasks.length}</span>
              </div>
            )}
            {group.tasks.map(task => {
              const done = task.status === 'done';
              const isSelected = selectedId === task.id;
              return (
                <div key={task.id} onClick={() => onSelect(task)}
                  className={`flex items-center h-10 border-b border-border/50 cursor-pointer transition-colors duration-75 mx-1 rounded-md ${
                    isSelected ? 'bg-bg-tertiary' : 'hover:bg-bg-secondary'
                  }`}>
                  {/* Checkbox */}
                  <div className="w-9 flex items-center justify-center shrink-0">
                    <button onClick={e => { e.stopPropagation(); update.mutate({ id: task.id, data: { status: done ? TaskStatus.TODO : TaskStatus.DONE } }); }}
                      className="w-4 h-4 rounded-full border-[1.5px] flex items-center justify-center cursor-pointer"
                      style={{ borderColor: done ? '#30D158' : 'rgba(255,245,235,0.15)', backgroundColor: done ? '#30D158' : 'transparent' }}>
                      {done && <svg width="8" height="6" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="#14130E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
                    </button>
                  </div>
                  {/* Title */}
                  <div className="flex-1 min-w-0 px-2">
                    <span className={`text-[15px] truncate block ${done ? 'text-text-quaternary line-through' : 'text-text-secondary'}`}
              >{task.title}</span>
                  </div>
                  {/* Status */}
                  <StatusCell status={task.status} />
                  {/* Priority */}
                  <PriorityCell priority={task.priority} />
                  {/* Project */}
                  <div className="px-2 text-[13px] text-text-quaternary truncate" style={{ width: 90, flexShrink: 0 }}>{task.project || '—'}</div>
                  {/* Due */}
                  <DueCell due={task.due} />
                  {/* Assignee */}
                  <div className="px-2 text-[13px] text-text-quaternary truncate flex items-center gap-1" style={{ width: 90, flexShrink: 0 }}>
                    {task.assigned_to ? <><User className="w-[10px] h-[10px] shrink-0" />{task.assigned_to}</> : '—'}
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// QuickCreate
// ═══════════════════════════════════════════════════════════════════════════

function QuickCreate({ onClose, onCreate }: { onClose: () => void; onCreate: (title: string) => void }) {
  const [title, setTitle] = useState('');
  return (
    <div className="flex items-center gap-3 h-11 px-3 mx-2 mb-2 rounded-lg bg-bg-secondary border border-accent/30">
      <div className="w-[18px] h-[18px] rounded-full border-[1.5px] border-accent/30 shrink-0" />
      <input value={title} onChange={e => setTitle(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && title.trim()) { onCreate(title.trim()); setTitle(''); } if (e.key === 'Escape') onClose(); }}
        placeholder="What needs to be done?" autoFocus
        className="flex-1 bg-transparent text-[16px] text-text outline-none placeholder:text-text-quaternary"
 />
      <span className="text-[12px] text-text-quaternary shrink-0">Enter to add · Esc to close</span>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Main
// ═══════════════════════════════════════════════════════════════════════════

type View = 'stream' | 'board' | 'list';

export default function TasksPage({ initialProjectFilter }: { initialProjectFilter?: string | null }) {
  const { data, isLoading } = useWork();
  const createTask = useCreateTask();
  const updateTask = useUpdateTask();
  const { openTask } = useTaskOverlay();
  const [view, setView] = useState<View>('stream');
  const [selected, setSelected] = useState<Task | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState('');
  const [showSearch, setShowSearch] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [filterProject, setFilterProject] = useState(initialProjectFilter ?? '');
  const [filterStatus, setFilterStatus] = useState<Set<string>>(new Set());
  const [filterPriority, setFilterPriority] = useState<Set<number>>(new Set());
  const [sortField, setSortField] = useState('priority');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [groupBy, setGroupBy] = useState('');
  const [activePopover, setActivePopover] = useState<'sort' | 'filter' | 'settings' | null>(null);
  const [visibleCols, setVisibleCols] = useState<Set<string>>(new Set(['title', 'status', 'priority', 'project', 'due', 'assigned_to']));

  const tasks = data?.tasks ?? [];
  const projects = useMemo(() => [...new Set(tasks.map(t => t.project).filter(Boolean) as string[])], [tasks]);

  const filtered = useMemo(() => {
    // 1. Filter
    let r = tasks;
    if (search) { const q = search.toLowerCase(); r = r.filter(t => t.title.toLowerCase().includes(q)); }
    if (filterProject) r = r.filter(t => t.project === filterProject);
    if (filterStatus.size > 0) r = r.filter(t => filterStatus.has(t.status));
    if (filterPriority.size > 0) r = r.filter(t => filterPriority.has(t.priority));

    // 2. Sort
    r = [...r].sort((a, b) => {
      let cmp = 0;
      if (sortField === 'title') cmp = a.title.localeCompare(b.title);
      else if (sortField === 'priority') cmp = a.priority - b.priority;
      else if (sortField === 'status') cmp = a.status.localeCompare(b.status);
      else if (sortField === 'project') cmp = (a.project ?? '').localeCompare(b.project ?? '');
      else if (sortField === 'due') cmp = (a.due ? new Date(a.due).getTime() : Infinity) - (b.due ? new Date(b.due).getTime() : Infinity);
      else if (sortField === 'assigned_to') cmp = (a.assigned_to ?? '').localeCompare(b.assigned_to ?? '');
      else if (sortField === 'created') cmp = new Date(a.created).getTime() - new Date(b.created).getTime();
      return sortDir === 'asc' ? cmp : -cmp;
    });

    return r;
  }, [tasks, search, filterProject, filterStatus, sortField, sortDir]);

  // Close popover on outside click
  useEffect(() => {
    if (!activePopover) return;
    const handler = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest('[data-popover]')) setActivePopover(null); };
    setTimeout(() => document.addEventListener('click', handler), 0);
    return () => document.removeEventListener('click', handler);
  }, [activePopover]);

  const hasActiveFilters = filterProject || filterStatus.size > 0 || filterPriority.size > 0;
  const filterCount = (filterStatus.size > 0 ? 1 : 0) + (filterPriority.size > 0 ? 1 : 0) + (filterProject ? 1 : 0);

  // Keep selected in sync with data updates
  useEffect(() => {
    if (selected) {
      const updated = tasks.find(t => t.id === selected.id);
      if (updated) setSelected(updated);
    }
  }, [tasks]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keyboard navigation
  useTaskKeyboard({
    tasks: filtered,
    focusedId,
    selectedId: selected?.id ?? null,
    onFocus: setFocusedId,
    onSelect: (task) => { if (task) openTask(task.id); else setSelected(null); },
    onToggleDone: (task) => updateTask.mutate({ id: task.id, data: { status: task.status === 'done' ? TaskStatus.TODO : TaskStatus.DONE } }),
    onSetPriority: (task, p) => updateTask.mutate({ id: task.id, data: { priority: p as TaskPriority } }),
    onSetStatus: (task, s) => updateTask.mutate({ id: task.id, data: { status: s as TaskStatus } }),
    onStartCreate: () => setCreating(true),
    onFocusSearch: () => { setShowSearch(true); setTimeout(() => searchInputRef.current?.focus(), 0); },
  });

  const handleCreate = useCallback((title: string) => {
    createTask.mutate({ title, priority: 3 as TaskPriority });
  }, [createTask]);

  // Row open / Enter → shared centered peek (replaces the old right-side panel).
  const openPeek = useCallback((t: Task) => openTask(t.id), [openTask]);

  const byStatus = useCallback((s: string) => filtered.filter(t => t.status === s), [filtered]);

  if (isLoading) return <div className="flex items-center justify-center h-full"><p className="text-text-quaternary">Loading tasks...</p></div>;

  return (
    <div className="flex h-full">
      {/* ── Left: task content ── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* ── Toolbar ── */}
        <div className="shrink-0 flex items-center gap-1 px-3 h-10 border-b border-border">
          {/* View switcher — left */}
          <div className="flex gap-0.5 text-[13px]">
            {(['stream', 'board', 'list'] as const).map(v => (
              <button key={v} onClick={() => setView(v)}
                className={`px-2 py-1 rounded-md cursor-pointer transition-colors duration-75 font-[510] ${
                  view === v ? 'bg-bg-tertiary text-text-secondary' : 'text-text-quaternary hover:text-text-tertiary'
                }`}>{v === 'stream' ? 'Stream' : v === 'board' ? 'Board' : 'List'}</button>
            ))}
          </div>

          <div className="flex-1" />

          {/* Right side: icons + New */}
          <div className="flex items-center gap-1">
            {/* ── Sort ── */}
            <div className="relative" data-popover>
              <button onClick={e => { e.stopPropagation(); setActivePopover(activePopover === 'sort' ? null : 'sort'); }}
                className={`h-7 px-2 flex items-center gap-1.5 rounded-md cursor-pointer hover:bg-bg-tertiary transition-colors duration-75 ${
                  activePopover === 'sort' ? 'bg-bg-tertiary text-text-secondary' : sortField !== 'priority' ? 'text-text-secondary' : 'text-text-quaternary hover:text-text-tertiary'
                }`} title="Sort">
                <ArrowUpDown className="w-3.5 h-3.5" />
                {sortField !== 'priority' && <span className="text-[12px] capitalize">{sortField}</span>}
              </button>
              {activePopover === 'sort' && (
                <div data-popover className="absolute right-0 top-full mt-1 w-[220px] bg-bg-panel border border-border-secondary rounded-lg shadow-[0_4px_20px_rgba(0,0,0,0.4)] z-50 py-1">
                  <p className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider px-3 py-1.5">Sort by</p>
                  {[
                    { id: 'priority', label: 'Priority', icon: '⬆' }, { id: 'title', label: 'Title', icon: 'Aa' },
                    { id: 'status', label: 'Status', icon: '●' }, { id: 'project', label: 'Project', icon: '📁' },
                    { id: 'due', label: 'Due date', icon: '📅' }, { id: 'assigned_to', label: 'Assignee', icon: '👤' },
                    { id: 'created', label: 'Created', icon: '🕐' },
                  ].map(opt => (
                    <button key={opt.id} onClick={() => {
                      if (sortField === opt.id) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
                      else { setSortField(opt.id); setSortDir('asc'); }
                    }}
                      className={`flex items-center gap-2.5 w-full px-3 py-1.5 text-[13px] cursor-pointer hover:bg-bg-secondary ${sortField === opt.id ? 'text-accent' : 'text-text-tertiary'}`}>
                      <span className="w-4 text-center text-[12px]">{opt.icon}</span>
                      <span className="flex-1">{opt.label}</span>
                      {sortField === opt.id && (
                        <span className="text-[11px] bg-bg-tertiary px-1.5 py-0.5 rounded">{sortDir === 'asc' ? '↑ Ascending' : '↓ Descending'}</span>
                      )}
                    </button>
                  ))}
                  <div className="border-t border-border my-1" />
                  <button onClick={() => { setSortField('priority'); setSortDir('asc'); setActivePopover(null); }}
                    className="w-full px-3 py-1.5 text-[13px] text-text-quaternary cursor-pointer hover:bg-bg-secondary">Reset to default</button>
                </div>
              )}
            </div>

            {/* ── Filter ── */}
            <div className="relative" data-popover>
              <button onClick={e => { e.stopPropagation(); setActivePopover(activePopover === 'filter' ? null : 'filter'); }}
                className={`h-7 px-2 flex items-center gap-1.5 rounded-md cursor-pointer hover:bg-bg-tertiary transition-colors duration-75 ${
                  activePopover === 'filter' ? 'bg-bg-tertiary text-text-secondary' : hasActiveFilters ? 'text-accent' : 'text-text-quaternary hover:text-text-tertiary'
                }`} title="Filter">
                <SlidersHorizontal className="w-3.5 h-3.5" />
                {filterCount > 0 && <span className="text-[11px] bg-accent text-on-accent w-4 h-4 rounded-full flex items-center justify-center font-[590]">{filterCount}</span>}
              </button>
              {activePopover === 'filter' && (
                <div data-popover className="absolute right-0 top-full mt-1 w-[240px] bg-bg-panel border border-border-secondary rounded-lg shadow-[0_4px_20px_rgba(0,0,0,0.4)] z-50 py-1 max-h-[400px] overflow-y-auto">
                  {/* Status */}
                  <p className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider px-3 py-1.5">Status</p>
                  <div className="px-2 pb-1 flex flex-wrap gap-1">
                    {['todo', 'active', 'waiting', 'done', 'cancelled'].map(s => (
                      <button key={s} onClick={() => setFilterStatus(prev => { const n = new Set(prev); if (n.has(s)) n.delete(s); else n.add(s); return n; })}
                        className={`flex items-center gap-1.5 h-6 px-2 rounded-md text-[12px] font-[510] cursor-pointer transition-all duration-75 border ${
                          filterStatus.has(s) ? 'border-accent/30 bg-accent/10 text-accent' : 'border-border text-text-tertiary hover:border-border-secondary'
                        }`}>
                        <div className="w-[5px] h-[5px] rounded-full" style={{ backgroundColor: STAT_COLOR[s] }} />
                        {STAT_LABEL[s]}
                      </button>
                    ))}
                  </div>

                  {/* Priority */}
                  <p className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider px-3 py-1.5 mt-1">Priority</p>
                  <div className="px-2 pb-1 flex gap-1">
                    {[1, 2, 3, 4, 5].map(p => (
                      <button key={p} onClick={() => setFilterPriority(prev => { const n = new Set(prev); if (n.has(p)) n.delete(p); else n.add(p); return n; })}
                        className={`flex items-center gap-1 h-6 px-2 rounded-md text-[12px] font-[510] cursor-pointer transition-all duration-75 border ${
                          filterPriority.has(p) ? 'border-accent/30 bg-accent/10 text-accent' : 'border-border text-text-tertiary hover:border-border-secondary'
                        }`}>
                        <div className="w-[4px] h-[4px] rounded-full" style={{ backgroundColor: PRI[p] }} />
                        P{p}
                      </button>
                    ))}
                  </div>

                  {/* Project */}
                  <p className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider px-3 py-1.5 mt-1">Project</p>
                  <div className="px-2 pb-1 flex flex-wrap gap-1">
                    {projects.map(p => (
                      <button key={p} onClick={() => setFilterProject(filterProject === p ? '' : p)}
                        className={`h-6 px-2 rounded-md text-[12px] font-[510] cursor-pointer transition-all duration-75 border ${
                          filterProject === p ? 'border-accent/30 bg-accent/10 text-accent' : 'border-border text-text-tertiary hover:border-border-secondary'
                        }`}>{p}</button>
                    ))}
                  </div>

                  {/* Clear */}
                  {hasActiveFilters && <>
                    <div className="border-t border-border my-1" />
                    <button onClick={() => { setFilterProject(''); setFilterStatus(new Set()); setFilterPriority(new Set()); }}
                      className="w-full px-3 py-1.5 text-[13px] text-text-quaternary cursor-pointer hover:bg-bg-secondary">Clear all filters</button>
                  </>}
                </div>
              )}
            </div>

            {/* ── Search ── */}
            <button onClick={() => setShowSearch(!showSearch)}
              className={`w-7 h-7 flex items-center justify-center rounded-md cursor-pointer hover:bg-bg-tertiary transition-colors duration-75 ${
                showSearch || search ? 'text-text-secondary' : 'text-text-quaternary hover:text-text-tertiary'
              }`} title="Search">
              <Search className="w-3.5 h-3.5" />
            </button>

            {/* ── Settings ── */}
            <div className="relative" data-popover>
              <button onClick={e => { e.stopPropagation(); setActivePopover(activePopover === 'settings' ? null : 'settings'); }}
                className={`w-7 h-7 flex items-center justify-center rounded-md cursor-pointer hover:bg-bg-tertiary transition-colors duration-75 ${
                  activePopover === 'settings' ? 'bg-bg-tertiary text-text-secondary' : 'text-text-quaternary hover:text-text-tertiary'
                }`} title="View settings">
                <Settings2 className="w-3.5 h-3.5" />
              </button>
              {activePopover === 'settings' && (
                <div data-popover className="absolute right-0 top-full mt-1 w-[240px] bg-bg-panel border border-border-secondary rounded-lg shadow-[0_4px_20px_rgba(0,0,0,0.4)] z-50 py-1">
                  <p className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider px-3 py-1.5">View settings</p>

                  {/* Group by */}
                  <div className="px-3 py-1.5 flex items-center justify-between">
                    <span className="text-[13px] text-text-tertiary">Group by</span>
                    <select value={groupBy} onChange={e => setGroupBy(e.target.value)}
                      className="text-[13px] bg-bg-tertiary text-text-secondary rounded-md px-2 py-0.5 border border-border outline-none cursor-pointer">
                      <option value="">None</option>
                      <option value="status">Status</option>
                      <option value="project">Project</option>
                      <option value="assigned_to">Assignee</option>
                    </select>
                  </div>

                  {/* Column visibility (list view only) */}
                  {view === 'list' && <>
                    <div className="border-t border-border my-1" />
                    <p className="text-[12px] font-[590] text-text-quaternary uppercase tracking-wider px-3 py-1.5">Properties</p>
                    {['title', 'status', 'priority', 'project', 'due', 'assigned_to', 'tags', 'created'].map(colId => {
                      const isOn = visibleCols.has(colId);
                      const isTitle = colId === 'title';
                      return (
                        <button key={colId} onClick={() => {
                          if (isTitle) return;
                          setVisibleCols(prev => { const n = new Set(prev); if (n.has(colId)) n.delete(colId); else n.add(colId); return n; });
                        }}
                          className={`flex items-center gap-2 w-full px-3 py-1.5 text-[13px] ${isTitle ? 'text-text-quaternary opacity-50 cursor-default' : 'cursor-pointer hover:bg-bg-secondary text-text-tertiary'}`}>
                          <span className="flex-1 capitalize">{colId === 'assigned_to' ? 'Assignee' : colId}</span>
                          {isOn
                            ? <span className="w-7 h-4 rounded-full bg-accent flex items-center justify-end px-0.5"><span className="w-3 h-3 rounded-full bg-bg" /></span>
                            : <span className="w-7 h-4 rounded-full bg-bg-quaternary flex items-center px-0.5"><span className="w-3 h-3 rounded-full bg-bg-secondary" /></span>
                          }
                        </button>
                      );
                    })}
                  </>}

                  {/* Info */}
                  <div className="border-t border-border my-1" />
                  <div className="px-3 py-1.5 flex items-center justify-between">
                    <span className="text-[13px] text-text-quaternary">Total tasks</span>
                    <span className="text-[13px] text-text-secondary font-mono">{filtered.length}</span>
                  </div>
                </div>
              )}
            </div>

            {/* ── New ── */}
            <button onClick={() => setCreating(true)}
              className="h-7 px-3 ml-1 flex items-center gap-1.5 rounded-lg bg-accent hover:bg-accent-hover text-[13px] font-[510] text-on-accent cursor-pointer transition-colors duration-75">
              <Plus className="w-3.5 h-3.5" />New
            </button>
          </div>
        </div>

        {/* Inline search bar — shows below toolbar when active */}
        {showSearch && (
          <div className="flex items-center gap-2 px-4 h-8 border-b border-border">
            <Search className="w-3.5 h-3.5 text-text-quaternary shrink-0" />
            <input ref={searchInputRef} value={search} onChange={e => setSearch(e.target.value)} placeholder="Type to search..."
              autoFocus className="flex-1 bg-transparent text-[14px] text-text outline-none placeholder:text-text-quaternary" />
            {search && <button onClick={() => setSearch('')} className="cursor-pointer"><X className="w-3 h-3 text-text-quaternary" /></button>}
            <button onClick={() => { setShowSearch(false); setSearch(''); }} className="cursor-pointer"><X className="w-3.5 h-3.5 text-text-quaternary" /></button>
          </div>
        )}

        {/* Quick create */}
        {creating && <QuickCreate onClose={() => setCreating(false)} onCreate={handleCreate} />}

        {/* Views */}
        <div className="flex-1 min-h-0 overflow-y-auto px-2">
          {view === 'stream' && (
            <>
              <StatusGroup status="active" tasks={byStatus('active')} onSelect={openPeek} selectedId={selected?.id} focusedId={focusedId ?? undefined} />
              <StatusGroup status="todo" tasks={byStatus('todo')} onSelect={openPeek} selectedId={selected?.id} focusedId={focusedId ?? undefined} />
              <StatusGroup status="waiting" tasks={byStatus('waiting')} onSelect={openPeek} selectedId={selected?.id} focusedId={focusedId ?? undefined} />
              <StatusGroup status="done" tasks={byStatus('done')} onSelect={openPeek} selectedId={selected?.id} focusedId={focusedId ?? undefined} defaultOpen={false} />
            </>
          )}
          {view === 'board' && (
            <BoardView tasks={filtered} onSelect={openPeek} focusedId={focusedId ?? undefined}
              onStatusChange={(taskId, newStatus) => updateTask.mutate({ id: taskId, data: { status: newStatus as TaskStatus } })} />
          )}
          {view === 'list' && <DatabaseView tasks={filtered} onSelect={openPeek} selectedId={selected?.id} projects={projects} groupBy={groupBy} onGroupByChange={setGroupBy} visibleCols={visibleCols} />}
          {/* Today view is now a top-level tab in Work.tsx */}

          {filtered.length === 0 && !creating && (
            <div className="flex flex-col items-center justify-center py-20">
              <p className="text-[16px] text-text-quaternary mb-1">
                {search || filterProject ? 'No tasks match your filters.' : 'No tasks yet.'}
              </p>
              <p className="text-[13px] text-text-quaternary opacity-50">
                Press <kbd className="px-1 py-0.5 rounded bg-bg-tertiary text-text-quaternary font-mono text-[12px]">N</kbd> to create one
              </p>
            </div>
          )}
        </div>
      </div>

      {/* ── Keyboard hint bar ── */}
      {focusedId && !selected && (
        <div className="shrink-0 flex items-center gap-4 px-4 h-7 border-t border-border text-[12px] text-text-quaternary">
          <span><kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px]">j</kbd><kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px] ml-0.5">k</kbd> navigate</span>
          <span><kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px]">Enter</kbd> open</span>
          <span><kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px]">x</kbd> toggle done</span>
          <span><kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px]">1</kbd>-<kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px]">5</kbd> priority</span>
          <span><kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px]">/</kbd> search</span>
          <span><kbd className="px-1 py-0.5 rounded bg-bg-tertiary font-mono text-[11px]">Esc</kbd> deselect</span>
        </div>
      )}

      {/* ── Detail panel — floating overlay, no backdrop blocking scroll ── */}
      {selected && (
        <>
          <div className="fixed top-3 right-3 bottom-3 w-[400px] max-w-[90vw] z-50 rounded-[14px] overflow-hidden shadow-[0_8px_40px_rgba(0,0,0,0.5)] border border-border-secondary animate-[slide-in-right_180ms_ease-out]">
            <DetailPanel task={selected} onClose={() => setSelected(null)} />
          </div>
          <style>{`@keyframes slide-in-right { from { transform: translateX(100%); opacity: 0.8; } to { transform: translateX(0); opacity: 1; } }`}</style>
        </>
      )}
    </div>
  );
}
