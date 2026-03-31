import { useState, useEffect, useRef, useCallback, useMemo, Fragment } from 'react';
import { Search, FolderOpen, FolderClosed, FileText, ChevronRight, ChevronDown, X, Clock, Tag as TagIcon, BookOpen, Menu, ArrowLeft } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

const API = '/api';

interface TreeNode { name: string; path: string; type: 'folder' | 'file'; children?: TreeNode[]; count?: number; }
interface VaultFile { path: string; name: string; content: string; body: string; frontmatter: Record<string, unknown> | null; }
interface SearchResult { path: string; name: string; snippet?: string; score?: number; collection?: string; }

function FolderTree({ nodes, selectedPath, onSelect, expandedPaths, onToggle, depth = 0 }: { nodes: TreeNode[]; selectedPath: string; onSelect: (path: string) => void; expandedPaths: Set<string>; onToggle: (path: string) => void; depth?: number }) {
  if (!Array.isArray(nodes)) return null;
  return (
    <div className={depth > 0 ? 'ml-3' : ''}>
      {nodes.map(node => (
        <Fragment key={node.path}>
          {node.type === 'folder' ? (
            <>
              <button onClick={() => onToggle(node.path)} className="w-full flex items-center gap-1.5 py-1.5 px-2 rounded-sm text-left hover:bg-hover transition-colors">
                {expandedPaths.has(node.path) ? <ChevronDown className="w-3.5 h-3.5 text-text-quaternary shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-text-quaternary shrink-0" />}
                {expandedPaths.has(node.path) ? <FolderOpen className="w-4 h-4 text-accent/70 shrink-0" /> : <FolderClosed className="w-4 h-4 text-text-tertiary shrink-0" />}
                <span className="text-[13px] text-text-secondary truncate">{node.name}</span>
                {node.count !== undefined && node.count > 0 && <span className="text-[11px] text-text-quaternary ml-auto shrink-0 tabular-nums">{node.count}</span>}
              </button>
              {expandedPaths.has(node.path) && node.children && <FolderTree nodes={node.children} selectedPath={selectedPath} onSelect={onSelect} expandedPaths={expandedPaths} onToggle={onToggle} depth={depth + 1} />}
            </>
          ) : (
            <button onClick={() => onSelect(node.path)} className={`w-full flex items-center gap-1.5 py-1.5 px-2 pl-[26px] rounded-sm text-left transition-colors ${selectedPath === node.path ? 'bg-accent/10 text-accent' : 'hover:bg-hover text-text-secondary'}`}>
              <FileText className={`w-4 h-4 shrink-0 ${selectedPath === node.path ? 'text-accent' : 'text-text-quaternary'}`} />
              <span className="text-[13px] truncate">{node.name.replace('.md', '')}</span>
            </button>
          )}
        </Fragment>
      ))}
    </div>
  );
}

function FrontmatterBar({ fm }: { fm: Record<string, unknown> }) {
  const badges: { label: string; value: string }[] = [];
  if (fm.type) badges.push({ label: 'type', value: String(fm.type) });
  if (fm.stage) badges.push({ label: 'stage', value: String(fm.stage) });
  if (fm.status) badges.push({ label: 'status', value: String(fm.status) });
  if (fm.project) badges.push({ label: 'project', value: String(fm.project) });
  if (!badges.length && !fm.date && !fm.tags) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 mt-3">
      {badges.map(b => <span key={b.label} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-bg-tertiary text-[11px]"><span className="text-text-quaternary uppercase tracking-wider">{b.label}</span><span className="text-text-tertiary">{b.value}</span></span>)}
      {fm.date && <span className="inline-flex items-center gap-1 text-[11px] text-text-quaternary"><Clock className="w-3 h-3" />{String(fm.date)}</span>}
      {fm.tags && <span className="inline-flex items-center gap-1 text-[11px] text-text-quaternary"><TagIcon className="w-3 h-3" />{Array.isArray(fm.tags) ? fm.tags.join(', ') : String(fm.tags)}</span>}
    </div>
  );
}

function MarkdownContent({ body }: { body: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
      h1: ({ children }) => <h1 className="text-[22px] font-[700] text-text tracking-[-0.02em] mt-10 mb-4 pb-2 border-b border-border">{children}</h1>,
      h2: ({ children }) => <h2 className="text-[18px] font-[650] text-text tracking-[-0.015em] mt-8 mb-3">{children}</h2>,
      h3: ({ children }) => <h3 className="text-[15px] font-[600] text-text tracking-[-0.01em] mt-6 mb-2">{children}</h3>,
      p: ({ children }) => <p className="text-[15px] leading-[1.75] text-text-secondary mb-4">{children}</p>,
      li: ({ children }) => <li className="text-[15px] leading-[1.75] text-text-secondary mb-1">{children}</li>,
      ul: ({ children }) => <ul className="list-disc pl-5 mb-4 space-y-0.5">{children}</ul>,
      ol: ({ children }) => <ol className="list-decimal pl-5 mb-4 space-y-0.5">{children}</ol>,
      a: ({ href, children }) => <a href={href} className="text-accent hover:text-accent/80 underline underline-offset-2 decoration-accent/30">{children}</a>,
      blockquote: ({ children }) => <blockquote className="border-l-[3px] border-accent/30 pl-4 my-4 text-text-tertiary italic">{children}</blockquote>,
      strong: ({ children }) => <strong className="font-[600] text-text">{children}</strong>,
      hr: () => <hr className="border-border my-8" />,
      table: ({ children }) => <div className="overflow-x-auto my-4 rounded-lg border border-border"><table className="w-full text-[13px]">{children}</table></div>,
      thead: ({ children }) => <thead className="bg-bg-tertiary">{children}</thead>,
      th: ({ children }) => <th className="px-3 py-2 text-left font-[600] text-text-secondary border-b border-border text-[12px]">{children}</th>,
      td: ({ children }) => <td className="px-3 py-2 text-text-secondary border-b border-border/50">{children}</td>,
      code: ({ className, children }) => {
        const match = /language-(\w+)/.exec(className || '');
        if (!match) return <code className="text-[13px] bg-bg-tertiary text-accent px-1.5 py-0.5 rounded-[4px] font-mono">{children}</code>;
        return <SyntaxHighlighter style={oneDark} language={match[1]} PreTag="div" customStyle={{ background: 'var(--bg-tertiary, #1a1a1a)', padding: '16px', borderRadius: '8px', fontSize: '13px', margin: '16px 0' }}>{String(children).replace(/\n$/, '')}</SyntaxHighlighter>;
      },
      pre: ({ children }) => <>{children}</>,
    }}>{body}</ReactMarkdown>
  );
}

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

  useEffect(() => {
    fetch(`${API}/vault/tree`)
      .then(r => {
        if (!r.ok) return [];
        const ct = r.headers.get('content-type') || '';
        if (!ct.includes('application/json')) return [];
        return r.json();
      })
      .then(d => setTree(Array.isArray(d) ? d : d?.tree ?? d?.nodes ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (!searchQuery || searchQuery.length < 2) { setSearchResults([]); return; }
    setSearching(true);
    searchTimeout.current = setTimeout(() => {
      fetch(`${API}/vault/search?q=${encodeURIComponent(searchQuery)}`).then(r => { if (!r.ok) throw new Error(); const ct = r.headers.get('content-type') || ''; if (!ct.includes('application/json')) throw new Error(); return r.json(); }).then(r => { setSearchResults(Array.isArray(r.results) ? r.results : Array.isArray(r) ? r : []); setSearching(false); }).catch(() => { setSearchResults([]); setSearching(false); });
    }, 300);
  }, [searchQuery]);

  const loadFile = useCallback(async (path: string) => {
    setSelectedPath(path); setSidebarOpen(false);
    try { const resp = await fetch(`${API}/vault/file/${encodeURIComponent(path)}`); setFile(await resp.json()); setSearchQuery(''); setSearchResults([]); contentRef.current?.scrollTo(0, 0); } catch { /* empty */ }
  }, []);

  const toggleFolder = useCallback((path: string) => { setExpandedPaths(prev => { const next = new Set(prev); next.has(path) ? next.delete(path) : next.add(path); return next; }); }, []);

  const breadcrumbs = useMemo(() => selectedPath ? selectedPath.split('/') : [], [selectedPath]);

  const sidebarContent = (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-border">
        <div className="flex items-center gap-2 px-2.5 py-2 rounded-sm bg-bg-tertiary border border-border-secondary">
          <Search className="w-4 h-4 text-text-quaternary shrink-0" />
          <input type="text" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="Search vault..." className="flex-1 text-[13px] bg-transparent text-text placeholder:text-text-quaternary outline-none" />
          {searchQuery && <button onClick={() => { setSearchQuery(''); setSearchResults([]); }} className="p-0.5"><X className="w-3.5 h-3.5 text-text-quaternary" /></button>}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {searchQuery && searchResults.length > 0 ? (
          <div>
            <div className="type-overline text-text-quaternary px-2 py-1 mb-1">{searchResults.length} results</div>
            {searchResults.map(r => (
              <button key={r.path} onClick={() => loadFile(r.path)} className="w-full text-left px-2 py-2.5 rounded-sm hover:bg-hover transition-colors">
                <div className="flex items-center gap-1.5"><FileText className="w-4 h-4 text-text-quaternary shrink-0" /><span className="text-[13px] text-text-secondary truncate">{r.name?.replace('.md', '') || r.path}</span></div>
                {r.snippet && <p className="text-[11px] text-text-quaternary mt-1 line-clamp-2 pl-5">{r.snippet}</p>}
                {r.score !== undefined && <div className="pl-5 mt-1"><div className="w-16 h-1 bg-bg-tertiary rounded-full overflow-hidden"><div className="h-full bg-accent rounded-full" style={{ width: `${Math.min(100, r.score * 100)}%` }} /></div></div>}
              </button>
            ))}
          </div>
        ) : searchQuery && !searching ? (
          <p className="text-[13px] text-text-quaternary text-center py-12">No results</p>
        ) : (
          <FolderTree nodes={tree} selectedPath={selectedPath} onSelect={loadFile} expandedPaths={expandedPaths} onToggle={toggleFolder} />
        )}
      </div>
    </div>
  );

  return (
    <div className="flex overflow-hidden h-full">
      {sidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden" onClick={() => setSidebarOpen(false)}>
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" />
          <div className="absolute left-0 top-0 bottom-0 w-[300px] bg-bg border-r border-border shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-3 py-2 border-b border-border"><span className="text-[13px] font-[590] text-text-secondary">Vault</span><button onClick={() => setSidebarOpen(false)} className="p-1.5 rounded-sm hover:bg-hover"><X className="w-4 h-4 text-text-tertiary" /></button></div>
            {sidebarContent}
          </div>
        </div>
      )}
      <div className="hidden md:flex w-[260px] shrink-0 border-r border-border flex-col bg-bg">{sidebarContent}</div>
      <div className="flex-1 flex flex-col min-w-0">
        {file ? (
          <>
            <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-border bg-bg/80 backdrop-blur-lg">
              <div className="flex items-center gap-2">
                <button onClick={() => setSidebarOpen(true)} className="md:hidden p-1.5 -ml-1.5 rounded-sm hover:bg-hover"><Menu className="w-4 h-4 text-text-tertiary" /></button>
                <button onClick={() => { setFile(null); setSelectedPath(''); }} className="md:hidden p-1.5 rounded-sm hover:bg-hover"><ArrowLeft className="w-4 h-4 text-text-tertiary" /></button>
                <div className="hidden md:flex items-center gap-1 text-[11px] text-text-quaternary flex-1 min-w-0">
                  <button onClick={() => { setFile(null); setSelectedPath(''); }} className="hover:text-text-tertiary shrink-0">vault</button>
                  {breadcrumbs.map((crumb, i) => <span key={i} className="flex items-center gap-1 min-w-0"><ChevronRight className="w-2.5 h-2.5 shrink-0" /><span className={`truncate ${i === breadcrumbs.length - 1 ? 'text-text-tertiary' : ''}`}>{crumb.replace('.md', '')}</span></span>)}
                </div>
                <h1 className="md:hidden text-[14px] font-[590] text-text truncate flex-1">{(file.frontmatter?.title as string) || file.name.replace('.md', '')}</h1>
              </div>
            </div>
            <div ref={contentRef} className="flex-1 overflow-y-auto">
              <div className="max-w-[720px] mx-auto px-4 sm:px-6 py-6 sm:py-8">
                <h1 className="hidden md:block text-[24px] font-[700] text-text tracking-[-0.025em] leading-tight">{(file.frontmatter?.title as string) || file.name.replace('.md', '')}</h1>
                {file.frontmatter && <FrontmatterBar fm={file.frontmatter} />}
                <div className="mt-6 sm:mt-8" />
                <MarkdownContent body={file.body} />
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col">
            <div className="md:hidden flex-1 overflow-y-auto">{sidebarContent}</div>
            <div className="hidden md:flex flex-1 flex-col items-center justify-center text-center">
              <BookOpen className="w-10 h-10 text-text-quaternary mb-3 opacity-30" />
              <h2 className="text-[17px] font-[600] text-text mb-1">Vault</h2>
              <p className="text-[13px] text-text-tertiary max-w-[320px]">Browse and search your knowledge base. Select a file from the sidebar.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
