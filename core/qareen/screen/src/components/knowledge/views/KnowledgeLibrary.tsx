import { useState, useEffect } from 'react';
import { FileText, AlertCircle, Link as LinkIcon, Clock } from 'lucide-react';
import { Skeleton } from '@/components/primitives/Skeleton';
import { MarkdownRenderer } from '@/components/primitives/MarkdownRenderer';

// ---------------------------------------------------------------------------
// Types (mirrors backend /api/knowledge/library contract)
// ---------------------------------------------------------------------------

interface DocRow {
  path: string;
  stage: number;
  type: string;
  title: string;
  topic: string | null;
  has_frontmatter: boolean;
  has_summary: boolean;
  has_concepts: boolean;
  has_topic: boolean;
  has_source_url: boolean;
  backlink_count: number;
  is_orphan: boolean;
  issues: string[];
  last_modified: string;
  word_count: number;
}

interface LibraryGroup {
  key: string;
  label: string;
  count: number;
  docs: DocRow[];
}

interface LibraryResponse {
  view: 'stage' | 'topic';
  total: number;
  groups: LibraryGroup[];
}

interface VaultFile {
  path: string;
  frontmatter: Record<string, any>;
  body: string;
  size: number;
  modified: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string | undefined | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function obsidianUri(path: string): string {
  // knowledge/captures/foo.md  →  obsidian://open?vault=vault&file=knowledge/captures/foo
  const noExt = path.replace(/\.md$/, '');
  return `obsidian://open?vault=vault&file=${encodeURIComponent(noExt)}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KnowledgeLibrary() {
  const [view, setView] = useState<'stage' | 'topic'>('stage');
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [libLoading, setLibLoading] = useState(true);
  const [libError, setLibError] = useState('');

  const [selectedGroupKey, setSelectedGroupKey] = useState<string | null>(null);
  const [selectedDocPath, setSelectedDocPath] = useState<string | null>(null);

  const [file, setFile] = useState<VaultFile | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState('');

  // Fetch library on view change
  useEffect(() => {
    setLibLoading(true);
    setLibError('');
    const params = new URLSearchParams();
    params.set('view', view);
    params.set('limit', '200');

    fetch(`/api/knowledge/library?${params}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: LibraryResponse) => {
        setLibrary(data);
        // Auto-select first group if none selected (or current one is gone)
        const groups = data.groups || [];
        const currentStillExists =
          selectedGroupKey && groups.some(g => g.key === selectedGroupKey);
        if (!currentStillExists) {
          setSelectedGroupKey(groups[0]?.key ?? null);
        }
        setLibLoading(false);
      })
      .catch(e => {
        setLibError(e.message || 'Failed to load library');
        setLibLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // Fetch file on selected doc change
  useEffect(() => {
    if (!selectedDocPath) {
      setFile(null);
      return;
    }
    setFileLoading(true);
    setFileError('');
    fetch(`/api/knowledge/library/file?path=${encodeURIComponent(selectedDocPath)}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: VaultFile) => {
        setFile(data);
        setFileLoading(false);
      })
      .catch(e => {
        setFileError(e.message || 'Failed to load file');
        setFileLoading(false);
      });
  }, [selectedDocPath]);

  const groups = library?.groups ?? [];
  const activeGroup = groups.find(g => g.key === selectedGroupKey) ?? null;
  const activeDocs = activeGroup?.docs ?? [];

  // -------------------------------------------------------------------------
  // Sub-renders
  // -------------------------------------------------------------------------

  const navPane = (
    <div className="lg:w-[220px] lg:shrink-0 lg:border-r lg:border-border flex flex-col lg:h-full">
      {/* Header */}
      <div className="px-3 py-3 border-b border-border flex items-center gap-1.5">
        <button
          onClick={() => setView('stage')}
          className={`h-6 px-2.5 rounded-full border text-[10px] font-[510] cursor-pointer transition-colors duration-75
            ${view === 'stage'
              ? 'bg-bg-tertiary border-border text-text'
              : 'bg-bg-secondary/50 border-border text-text-tertiary hover:text-text hover:bg-bg-tertiary'}`}
        >
          By Stage
        </button>
        <button
          onClick={() => setView('topic')}
          className={`h-6 px-2.5 rounded-full border text-[10px] font-[510] cursor-pointer transition-colors duration-75
            ${view === 'topic'
              ? 'bg-bg-tertiary border-border text-text'
              : 'bg-bg-secondary/50 border-border text-text-tertiary hover:text-text hover:bg-bg-tertiary'}`}
        >
          By Topic
        </button>
      </div>

      {/* Groups */}
      <div className="flex-1 overflow-y-auto lg:max-h-full max-h-[240px]">
        {libLoading && (
          <div className="p-3 space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-4 w-full" />
            ))}
          </div>
        )}
        {libError && !libLoading && (
          <p className="px-3 py-3 text-[11px] text-red">Couldn't load — {libError}</p>
        )}
        {!libLoading && !libError && groups.length === 0 && (
          <p className="px-3 py-3 text-[11px] text-text-quaternary">No documents</p>
        )}
        {!libLoading && groups.map(g => {
          const isActive = g.key === selectedGroupKey;
          return (
            <button
              key={g.key}
              onClick={() => {
                setSelectedGroupKey(g.key);
                setSelectedDocPath(null);
              }}
              className={`w-full text-left px-3 py-1.5 border-b border-border flex items-center gap-2 cursor-pointer transition-colors duration-75
                ${isActive ? 'bg-bg-tertiary' : 'hover:bg-bg-secondary/50'}`}
            >
              <span className={`text-[12px] flex-1 min-w-0 truncate ${isActive ? 'text-text font-[530]' : 'text-text-secondary'}`}>
                {g.label}
              </span>
              <span className="text-[10px] font-mono tabular-nums text-text-quaternary shrink-0">
                {g.count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );

  const listPane = (
    <div className="lg:w-[280px] lg:shrink-0 lg:border-r lg:border-border flex flex-col lg:h-full">
      {/* Header */}
      <div className="px-3 py-3 border-b border-border flex items-center gap-2">
        <span className="text-[12px] text-text flex-1 min-w-0 truncate font-[530]">
          {activeGroup?.label ?? 'No group selected'}
        </span>
        {activeGroup && (
          <span className="text-[10px] font-mono tabular-nums text-text-quaternary shrink-0">
            {activeGroup.count}
          </span>
        )}
      </div>

      {/* Docs */}
      <div className="flex-1 overflow-y-auto lg:max-h-full max-h-[320px]">
        {!activeGroup && !libLoading && (
          <p className="px-3 py-3 text-[11px] text-text-quaternary">Select a group</p>
        )}
        {activeDocs.map(doc => {
          const isActive = doc.path === selectedDocPath;
          return (
            <button
              key={doc.path}
              onClick={() => setSelectedDocPath(doc.path)}
              className={`w-full text-left px-3 py-2 border-b border-border cursor-pointer transition-colors duration-75
                ${isActive ? 'bg-bg-tertiary' : 'hover:bg-bg-secondary/50'}`}
            >
              <div className={`text-[12px] leading-snug truncate ${isActive ? 'text-text font-[530]' : 'text-text-secondary'}`}>
                {doc.title}
              </div>
              <div className="flex items-center gap-2 mt-1 min-w-0">
                <span className="text-[10px] uppercase tracking-wide text-accent shrink-0">
                  {doc.type}
                </span>
                <span className="text-[10px] font-mono tabular-nums text-text-quaternary shrink-0">
                  {doc.word_count}w
                </span>
                {doc.is_orphan && (
                  <span className="text-[10px] text-yellow shrink-0">orphan</span>
                )}
                {doc.issues.length > 0 && (
                  <span className="text-[10px] text-red shrink-0 flex items-center gap-0.5">
                    <AlertCircle className="w-2.5 h-2.5" />
                    {doc.issues.length}
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );

  const readerPane = (
    <div className="flex-1 lg:h-full overflow-y-auto min-w-0">
      {!selectedDocPath && (
        <div className="h-full min-h-[300px] flex flex-col items-center justify-center text-text-tertiary px-6 py-12">
          <FileText className="w-8 h-8 mb-3 opacity-50" />
          <p className="text-[13px]">Select a document</p>
        </div>
      )}

      {selectedDocPath && fileLoading && (
        <div className="max-w-[720px] mx-auto px-6 py-8 space-y-3">
          <Skeleton className="h-3 w-1/3" />
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-5/6" />
          <Skeleton className="h-3 w-4/6" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-3/4" />
        </div>
      )}

      {selectedDocPath && fileError && !fileLoading && (
        <div className="max-w-[720px] mx-auto px-6 py-8">
          <p className="text-[12px] text-red">Couldn't load file — {fileError}</p>
        </div>
      )}

      {selectedDocPath && file && !fileLoading && (() => {
        // Find the corresponding DocRow for metadata
        const docRow = activeDocs.find(d => d.path === selectedDocPath);
        const fm = file.frontmatter || {};
        const title = (fm.title as string) || docRow?.title || selectedDocPath;
        const stage = docRow?.stage ?? (typeof fm.stage === 'number' ? fm.stage : null);
        const type = docRow?.type ?? (fm.type as string) ?? null;
        const topic = docRow?.topic ?? (fm.topic as string) ?? null;
        const tags: string[] = Array.isArray(fm.tags) ? fm.tags : [];
        const backlinks = docRow?.backlink_count ?? 0;
        const wordCount = docRow?.word_count ?? 0;
        const issues = docRow?.issues ?? [];
        const modified = file.modified || docRow?.last_modified || '';

        return (
          <div className="max-w-[720px] mx-auto px-6 py-8">
            {/* Top bar */}
            <div className="flex items-center gap-3 mb-5">
              <span className="text-[11px] font-mono text-text-quaternary truncate flex-1 min-w-0">
                {file.path}
              </span>
              <a
                href={obsidianUri(file.path)}
                className="text-[11px] text-accent hover:text-accent-hover transition-colors duration-75 flex items-center gap-1 shrink-0"
              >
                <LinkIcon className="w-3 h-3" />
                Open in Obsidian
              </a>
              <span className="text-[11px] font-mono tabular-nums text-text-quaternary shrink-0">
                {wordCount}w
              </span>
            </div>

            {/* Title */}
            <h1 className="text-[20px] font-serif text-text mb-4 leading-tight">
              {title}
            </h1>

            {/* Metadata card */}
            <div className="bg-bg-secondary/30 border border-border rounded-xl p-3 mb-5 flex flex-wrap items-center gap-x-3 gap-y-2">
              {stage !== null && (
                <span className="text-[10px] uppercase tracking-wide text-text-tertiary">
                  stage <span className="font-mono tabular-nums text-text-secondary">{stage}</span>
                </span>
              )}
              {type && (
                <span className="text-[10px] uppercase tracking-wide text-accent">
                  {type}
                </span>
              )}
              {topic && (
                <span className="text-[10px] text-text-secondary px-1.5 py-0.5 rounded-full border border-border">
                  {topic}
                </span>
              )}
              {tags.map(t => (
                <span
                  key={t}
                  className="text-[10px] text-text-tertiary px-1.5 py-0.5 rounded-full border border-border"
                >
                  #{t}
                </span>
              ))}
              <span className="text-[10px] text-text-tertiary flex items-center gap-1">
                <LinkIcon className="w-2.5 h-2.5" />
                <span className="font-mono tabular-nums">{backlinks}</span> backlinks
              </span>
              {modified && (
                <span className="text-[10px] text-text-tertiary flex items-center gap-1">
                  <Clock className="w-2.5 h-2.5" />
                  {formatDate(modified)}
                </span>
              )}
            </div>

            {/* Issues panel */}
            {issues.length > 0 && (
              <div className="border border-red/30 bg-red/5 rounded-xl p-3 mb-5">
                <p className="text-[11px] text-red">
                  Contract violations: {issues.join(', ')}
                </p>
              </div>
            )}

            {/* Body */}
            <MarkdownRenderer
              content={file.body}
              className="font-serif text-[14px] leading-relaxed"
            />
          </div>
        );
      })()}
    </div>
  );

  // -------------------------------------------------------------------------
  // Layout
  // -------------------------------------------------------------------------

  return (
    <div className="h-full overflow-hidden flex flex-col lg:flex-row">
      {navPane}
      {listPane}
      {readerPane}
    </div>
  );
}
