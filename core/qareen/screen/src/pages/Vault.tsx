import { useState, useEffect, useRef, useCallback, useMemo, Fragment } from 'react';
import { Search, FolderOpen, FolderClosed, FileText, ChevronRight, ChevronDown, X, Clock, BookOpen, Menu, ArrowLeft, Layers, Hash } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { Tag, type TagColor } from '@/components/primitives/Tag';

const API = '/api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TreeNode { name: string; path: string; type: 'folder' | 'file'; children?: TreeNode[]; count?: number; }
interface VaultFile { path: string; name: string; title?: string; content: string; body?: string; frontmatter: Record<string, unknown> | null; size_bytes?: number; }
interface SearchResult { path: string; name: string; snippet?: string; score?: number; collection?: string; }

// ---------------------------------------------------------------------------
// Stage metadata
// ---------------------------------------------------------------------------

const stageLabels: Record<number, string> = {
  1: 'Capture',
  2: 'Triage',
  3: 'Research',
  4: 'Synthesis',
  5: 'Decision',
  6: 'Expertise',
};

const stageColors: Record<number, TagColor> = {
  1: 'gray',
  2: 'yellow',
  3: 'blue',
  4: 'purple',
  5: 'green',
  6: 'orange',
};

const statusColors: Record<string, TagColor> = {
  active: 'green',
  draft: 'yellow',
  review: 'blue',
  archived: 'gray',
  done: 'green',
  blocked: 'red',
  paused: 'orange',
};

const typeColors: Record<string, TagColor> = {
  capture: 'gray',
  research: 'blue',
  synthesis: 'purple',
  decision: 'green',
  expertise: 'orange',
  reference: 'teal',
  initiative: 'yellow',
  daily: 'gray',
  weekly: 'blue',
  session: 'purple',
};

function tagColorForValue(key: string, value: string): TagColor {
  if (key === 'stage') return stageColors[Number(value)] || 'gray';
  if (key === 'status') return statusColors[value.toLowerCase()] || 'gray';
  if (key === 'type') return typeColors[value.toLowerCase()] || 'gray';
  return 'gray';
}

// ---------------------------------------------------------------------------
// Folder Tree
// ---------------------------------------------------------------------------

function FolderTree({ nodes, selectedPath, onSelect, expandedPaths, onToggle, depth = 0 }: { nodes: TreeNode[]; selectedPath: string; onSelect: (path: string) => void; expandedPaths: Set<string>; onToggle: (path: string) => void; depth?: number }) {
  if (!Array.isArray(nodes)) return null;
  return (
    <div className={depth > 0 ? 'ml-3' : ''}>
      {nodes.map(node => (
        <Fragment key={node.path}>
          {node.type === 'folder' ? (
            <>
              <button
                onClick={() => onToggle(node.path)}
                className="w-full flex items-center gap-1.5 py-2 px-2.5 rounded-[5px] text-left hover:bg-hover transition-colors cursor-pointer"
                style={{ transitionDuration: 'var(--duration-instant)' }}
              >
                {expandedPaths.has(node.path)
                  ? <ChevronDown className="w-3 h-3 text-text-quaternary shrink-0" />
                  : <ChevronRight className="w-3 h-3 text-text-quaternary shrink-0" />
                }
                {expandedPaths.has(node.path)
                  ? <FolderOpen className="w-4 h-4 text-accent/70 shrink-0" />
                  : <FolderClosed className="w-4 h-4 text-text-tertiary shrink-0" />
                }
                <span className="text-[13px] text-text-secondary truncate flex-1">{node.name}</span>
                {node.count !== undefined && node.count > 0 && (
                  <span className="text-[10px] text-text-quaternary ml-auto shrink-0 tabular-nums bg-bg-tertiary rounded-xs px-1.5 py-0.5 leading-none">{node.count}</span>
                )}
              </button>
              {expandedPaths.has(node.path) && node.children && (
                <FolderTree nodes={node.children} selectedPath={selectedPath} onSelect={onSelect} expandedPaths={expandedPaths} onToggle={onToggle} depth={depth + 1} />
              )}
            </>
          ) : (
            <button
              onClick={() => onSelect(node.path)}
              className={`
                w-full flex items-center gap-1.5 py-2 px-2.5 pl-[26px] rounded-[5px] text-left transition-colors cursor-pointer
                ${selectedPath === node.path
                  ? 'bg-accent/10 text-accent'
                  : 'hover:bg-hover text-text-secondary'
                }
              `}
              style={{ transitionDuration: 'var(--duration-instant)' }}
            >
              <FileText className={`w-4 h-4 shrink-0 ${selectedPath === node.path ? 'text-accent' : 'text-text-quaternary'}`} />
              <span className="text-[13px] truncate">{node.name.replace('.md', '')}</span>
            </button>
          )}
        </Fragment>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Search Result Card — document feel
// ---------------------------------------------------------------------------

function SearchResultCard({ result, onSelect }: { result: SearchResult; onSelect: () => void }) {
  const displayName = result.name?.replace('.md', '') || result.path.split('/').pop()?.replace('.md', '') || result.path;
  const collection = result.collection || result.path.split('/')[0];

  return (
    <button
      onClick={onSelect}
      className="w-full text-left px-3 py-3.5 rounded-[5px] hover:bg-hover transition-all cursor-pointer group border border-transparent hover:border-border"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      <div className="flex items-start gap-2.5">
        <FileText className="w-4 h-4 text-text-quaternary shrink-0 mt-0.5 group-hover:text-accent transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }} />
        <div className="flex-1 min-w-0">
          {/* Title — serif */}
          <span className="text-[14px] font-serif font-[500] text-text-secondary group-hover:text-text truncate block leading-tight transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
            {displayName}
          </span>
          {/* Collection badge */}
          <div className="flex items-center gap-2 mt-1.5">
            <Tag label={collection} color="gray" size="sm" />
            {result.score !== undefined && (
              <div className="flex items-center gap-1.5">
                <div className="w-12 h-1 bg-bg-tertiary rounded-full overflow-hidden">
                  <div className="h-full bg-accent/60 rounded-full" style={{ width: `${Math.min(100, result.score * 100)}%` }} />
                </div>
                <span className="text-[9px] text-text-quaternary tabular-nums">{Math.round(result.score * 100)}</span>
              </div>
            )}
          </div>
          {/* Snippet — serif */}
          {result.snippet && (
            <p className="text-[12px] font-serif text-text-quaternary mt-1.5 line-clamp-2 leading-[1.5]">{result.snippet}</p>
          )}
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Frontmatter Bar — warm tag colors per design system
// ---------------------------------------------------------------------------

function FrontmatterBar({ fm }: { fm: Record<string, unknown> }) {
  const badges: { label: string; value: string; color: TagColor }[] = [];
  if (fm.type) badges.push({ label: 'type', value: String(fm.type), color: tagColorForValue('type', String(fm.type)) });
  if (fm.stage) {
    const stageNum = Number(fm.stage);
    const label = stageLabels[stageNum] || `Stage ${fm.stage}`;
    badges.push({ label: `stage ${fm.stage}`, value: label, color: tagColorForValue('stage', String(fm.stage)) });
  }
  if (fm.status) badges.push({ label: 'status', value: String(fm.status), color: tagColorForValue('status', String(fm.status)) });
  if (fm.project) badges.push({ label: 'project', value: String(fm.project), color: 'purple' });

  if (!badges.length && !fm.date && !fm.tags) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 mt-4">
      {badges.map(b => (
        <Tag key={b.label} label={`${b.value}`} color={b.color} size="sm" icon={b.label.startsWith('stage') ? <Layers className="w-3 h-3" /> : undefined} />
      ))}
      {fm.date && (
        <span className="inline-flex items-center gap-1.5 text-[11px] text-text-quaternary">
          <Clock className="w-3 h-3" />
          <span>{String(fm.date)}</span>
        </span>
      )}
      {fm.tags && (
        <span className="inline-flex items-center gap-1.5 text-[11px] text-text-quaternary">
          <Hash className="w-3 h-3" />
          <span>{Array.isArray(fm.tags) ? fm.tags.join(', ') : String(fm.tags)}</span>
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Markdown Content — serif-first, warm
// ---------------------------------------------------------------------------

function MarkdownContent({ body }: { body: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      h1: ({ children }) => <h1 className="text-[24px] font-serif font-[700] text-text tracking-[-0.02em] mt-10 mb-4 pb-3 border-b border-border">{children}</h1>,
      h2: ({ children }) => <h2 className="text-[19px] font-serif font-[650] text-text tracking-[-0.015em] mt-8 mb-3">{children}</h2>,
      h3: ({ children }) => <h3 className="text-[16px] font-serif font-[600] text-text tracking-[-0.01em] mt-6 mb-2">{children}</h3>,
      p: ({ children }) => <p className="text-[15px] font-serif leading-[1.75] text-text-secondary mb-4">{children}</p>,
      li: ({ children }) => <li className="text-[15px] font-serif leading-[1.75] text-text-secondary mb-1">{children}</li>,
      ul: ({ children }) => <ul className="list-disc pl-5 mb-4 space-y-0.5">{children}</ul>,
      ol: ({ children }) => <ol className="list-decimal pl-5 mb-4 space-y-0.5">{children}</ol>,
      a: ({ href, children }) => <a href={href} className="text-accent hover:text-accent-hover underline underline-offset-2 decoration-accent/30 transition-colors cursor-pointer" style={{ transitionDuration: 'var(--duration-instant)' }}>{children}</a>,
      blockquote: ({ children }) => <blockquote className="border-l-[3px] border-accent/30 pl-4 my-4 text-text-tertiary italic font-serif">{children}</blockquote>,
      strong: ({ children }) => <strong className="font-[600] text-text">{children}</strong>,
      hr: () => <hr className="border-border my-8" />,
      table: ({ children }) => <div className="overflow-x-auto my-4 rounded-[7px] border border-border"><table className="w-full text-[13px]">{children}</table></div>,
      thead: ({ children }) => <thead className="bg-bg-tertiary">{children}</thead>,
      th: ({ children }) => <th className="px-3 py-2 text-left font-[600] text-text-secondary border-b border-border text-[12px]">{children}</th>,
      td: ({ children }) => <td className="px-3 py-2 text-text-secondary border-b border-border/50 text-[13px]">{children}</td>,
      code: ({ className, children }) => {
        const match = /language-(\w+)/.exec(className || '');
        if (!match) return <code className="text-[13px] bg-bg-tertiary text-accent px-1.5 py-0.5 rounded-[4px] font-mono">{children}</code>;
        return <SyntaxHighlighter style={oneDark} language={match[1]} PreTag="div" customStyle={{ background: 'var(--bg-tertiary, #2A2520)', padding: '16px', borderRadius: '7px', fontSize: '13px', margin: '16px 0', border: '1px solid rgba(255, 245, 235, 0.06)' }}>{String(children).replace(/\n$/, '')}</SyntaxHighlighter>;
      },
      pre: ({ children }) => <>{children}</>,
    }}>{body}</ReactMarkdown>
  );
}

// ---------------------------------------------------------------------------
// Main Vault Page
// ---------------------------------------------------------------------------

export default function VaultPage() {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [file, setFile] = useState<VaultFile | null>(null);
  const [selectedPath, setSelectedPath] = useState('');
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set(['knowledge', 'log']));
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Fetch tree
  useEffect(() => {
    fetch(`${API}/vault/tree`)
      .then(r => {
        if (!r.ok) throw new Error('tree endpoint unavailable');
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) throw new Error('not json');
        return r.json();
      })
      .then(d => setTree(Array.isArray(d) ? d : d?.tree ?? d?.nodes ?? []))
      .catch(() => {
        fetch(`${API}/vault/collections`)
          .then(r => r.ok ? r.json() : null)
          .then(d => {
            if (!d?.collections) return;
            const nodes: TreeNode[] = d.collections.map((c: { name: string; doc_count: number }) => ({
              name: c.name,
              path: c.name,
              type: 'folder' as const,
              children: [],
              count: c.doc_count,
            }));
            setTree(nodes);
          })
          .catch(() => {});
      });
  }, []);

  // Search with debounce
  useEffect(() => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (!searchQuery || searchQuery.length < 2) { setSearchResults([]); setSearching(false); return; }
    setSearching(true);
    searchTimeout.current = setTimeout(() => {
      fetch(`${API}/vault/search?q=${encodeURIComponent(searchQuery)}`)
        .then(r => {
          if (!r.ok) throw new Error();
          const ct = r.headers.get('content-type') || '';
          if (!ct.includes('application/json')) throw new Error();
          return r.json();
        })
        .then(r => {
          setSearchResults(Array.isArray(r.results) ? r.results : Array.isArray(r) ? r : []);
          setSearching(false);
        })
        .catch(() => { setSearchResults([]); setSearching(false); });
    }, 300);
  }, [searchQuery]);

  const loadFile = useCallback(async (path: string) => {
    setSelectedPath(path);
    setSidebarOpen(false);
    try {
      const resp = await fetch(`${API}/vault/file/${encodeURIComponent(path)}`);
      const data = await resp.json();
      data.name = data.name || path.split('/').pop() || path;
      setFile(data);
      setSearchQuery('');
      setSearchResults([]);
      contentRef.current?.scrollTo(0, 0);
    } catch { /* empty */ }
  }, []);

  const toggleFolder = useCallback((path: string) => {
    setExpandedPaths(prev => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });
  }, []);

  const breadcrumbs = useMemo(() => selectedPath ? selectedPath.split('/') : [], [selectedPath]);

  // Keyboard shortcut: Cmd+K or / to focus search
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey && e.key === 'k') || (e.key === '/' && !file && document.activeElement?.tagName !== 'INPUT')) {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [file]);

  // --- Sidebar content ---
  const sidebarContent = (
    <div className="flex flex-col h-full">
      {/* Search */}
      <div className="p-3 border-b border-border">
        <div className="flex items-center gap-2.5 px-3 py-2.5 rounded-[5px] bg-bg-tertiary border border-border-secondary transition-colors focus-within:border-border-tertiary" style={{ transitionDuration: 'var(--duration-fast)' }}>
          <Search className="w-4 h-4 text-text-quaternary shrink-0" />
          <input
            ref={searchInputRef}
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search vault..."
            className="flex-1 text-[13px] bg-transparent text-text placeholder:text-text-quaternary outline-none"
          />
          {searchQuery ? (
            <button onClick={() => { setSearchQuery(''); setSearchResults([]); }} className="p-0.5 rounded-xs hover:bg-hover transition-colors cursor-pointer" style={{ transitionDuration: 'var(--duration-instant)' }}>
              <X className="w-3.5 h-3.5 text-text-quaternary" />
            </button>
          ) : (
            <kbd className="text-[9px] text-text-quaternary bg-bg-secondary border border-border rounded-xs px-1.5 py-0.5 leading-none">/</kbd>
          )}
        </div>
      </div>

      {/* Tree or search results */}
      <div className="flex-1 overflow-y-auto p-2">
        {searchQuery && searchResults.length > 0 ? (
          <div>
            <div className="type-overline text-text-quaternary px-2 py-1.5 mb-1">{searchResults.length} results</div>
            {searchResults.map(r => (
              <SearchResultCard key={r.path} result={r} onSelect={() => loadFile(r.path)} />
            ))}
          </div>
        ) : searchQuery && searching ? (
          <div className="flex flex-col items-center justify-center py-16">
            <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin mb-3" />
            <p className="text-[12px] text-text-quaternary">Searching...</p>
          </div>
        ) : searchQuery && !searching ? (
          <div className="flex flex-col items-center justify-center py-16">
            <Search className="w-8 h-8 text-text-quaternary opacity-20 mb-3" />
            <p className="text-[13px] text-text-quaternary">No results for "{searchQuery}"</p>
            <p className="text-[11px] text-text-quaternary mt-1">Try different keywords</p>
          </div>
        ) : (
          <FolderTree nodes={tree} selectedPath={selectedPath} onSelect={loadFile} expandedPaths={expandedPaths} onToggle={toggleFolder} />
        )}
      </div>
    </div>
  );

  return (
    <div className="flex overflow-hidden h-full bg-bg">
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden" onClick={() => setSidebarOpen(false)}>
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity" style={{ transitionDuration: 'var(--duration-fast)' }} />
          <div className="absolute left-0 top-0 bottom-0 w-[300px] bg-bg border-r border-border shadow-[var(--shadow-high)]" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <span className="text-[14px] font-serif font-[600] text-text">Vault</span>
              <button onClick={() => setSidebarOpen(false)} className="p-1.5 rounded-[5px] hover:bg-hover cursor-pointer transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                <X className="w-4 h-4 text-text-tertiary" />
              </button>
            </div>
            {sidebarContent}
          </div>
        </div>
      )}

      {/* Desktop sidebar */}
      <div className="hidden md:flex w-[280px] shrink-0 border-r border-border flex-col bg-bg">
        {sidebarContent}
      </div>

      {/* Content area */}
      <div className="flex-1 flex flex-col min-w-0">
        {file ? (
          <>
            {/* Breadcrumb bar */}
            <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-border">
              <div className="flex items-center gap-2">
                <button onClick={() => setSidebarOpen(true)} className="md:hidden p-1.5 -ml-1.5 rounded-[5px] hover:bg-hover cursor-pointer transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                  <Menu className="w-4 h-4 text-text-tertiary" />
                </button>
                <button onClick={() => { setFile(null); setSelectedPath(''); }} className="md:hidden p-1.5 rounded-[5px] hover:bg-hover cursor-pointer transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>
                  <ArrowLeft className="w-4 h-4 text-text-tertiary" />
                </button>
                <div className="hidden md:flex items-center gap-1 text-[11px] text-text-quaternary flex-1 min-w-0">
                  <button onClick={() => { setFile(null); setSelectedPath(''); }} className="hover:text-text-tertiary shrink-0 cursor-pointer transition-colors" style={{ transitionDuration: 'var(--duration-instant)' }}>vault</button>
                  {breadcrumbs.map((crumb, i) => (
                    <span key={i} className="flex items-center gap-1 min-w-0">
                      <ChevronRight className="w-2.5 h-2.5 shrink-0" />
                      <span className={`truncate ${i === breadcrumbs.length - 1 ? 'text-text-tertiary font-[510]' : ''}`}>{crumb.replace('.md', '')}</span>
                    </span>
                  ))}
                </div>
                <h1 className="md:hidden text-[14px] font-serif font-[590] text-text truncate flex-1">{(file.frontmatter?.title as string) || file.title || file.name.replace('.md', '')}</h1>
              </div>
            </div>

            {/* Document content */}
            <div ref={contentRef} className="flex-1 overflow-y-auto">
              <div className="max-w-[720px] mx-auto px-6 sm:px-8 py-8 sm:py-10">
                {/* Document title — serif, large */}
                <h1 className="hidden md:block text-[26px] font-serif font-[700] text-text tracking-[-0.025em] leading-[1.2]">
                  {(file.frontmatter?.title as string) || file.title || file.name.replace('.md', '')}
                </h1>
                {file.frontmatter && <FrontmatterBar fm={file.frontmatter} />}
                <div className="mt-8" />
                <MarkdownContent body={file.body || file.content} />
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col">
            {/* Mobile: show sidebar content inline */}
            <div className="md:hidden flex-1 overflow-y-auto">{sidebarContent}</div>

            {/* Desktop: empty state */}
            <div className="hidden md:flex flex-1 flex-col items-center justify-center text-center px-6">
              <BookOpen className="w-12 h-12 text-text-quaternary mb-4 opacity-20" />
              <h2 className="text-[18px] font-serif font-[600] text-text mb-2">Your knowledge vault</h2>
              <p className="text-[13px] font-serif text-text-tertiary max-w-[360px] leading-[1.6]">
                Browse your collected knowledge, research, and decisions. Select a document from the sidebar, or press <kbd className="text-[11px] bg-bg-tertiary border border-border rounded-xs px-1.5 py-0.5 font-sans">/</kbd> to search.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
