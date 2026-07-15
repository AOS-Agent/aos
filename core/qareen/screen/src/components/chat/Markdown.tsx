import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { useState, useCallback } from 'react';
import { Copy, Check } from 'lucide-react';

const aosCodeTheme: Record<string, React.CSSProperties> = {
  'pre[class*="language-"]': {
    background: 'var(--color-bg)',
    color: '#E7E4DE',
    fontSize: '12.5px',
    lineHeight: '1.6',
    fontFamily: 'var(--font-mono)',
    margin: 0,
    padding: '14px',
    overflow: 'auto',
  },
  'code[class*="language-"]': {
    background: 'none',
    color: '#E7E4DE',
    fontSize: '12.5px',
    lineHeight: '1.6',
    fontFamily: 'var(--font-mono)',
  },
  comment: { color: '#66625B' },
  prolog: { color: '#66625B' },
  doctype: { color: '#66625B' },
  cdata: { color: '#66625B' },
  punctuation: { color: '#9A9590' },
  property: { color: '#D6CCB4' },
  tag: { color: '#D6CCB4' },
  boolean: { color: '#D6CCB4' },
  number: { color: '#D6CCB4' },
  constant: { color: '#D6CCB4' },
  symbol: { color: '#D6CCB4' },
  selector: { color: '#3DAD6A' },
  'attr-name': { color: '#3DAD6A' },
  string: { color: '#3DAD6A' },
  char: { color: '#3DAD6A' },
  builtin: { color: '#3DAD6A' },
  operator: { color: '#E7E4DE' },
  entity: { color: '#5A9FC4' },
  url: { color: '#5A9FC4' },
  variable: { color: '#E7E4DE' },
  atrule: { color: '#5A9FC4' },
  'attr-value': { color: '#5A9FC4' },
  keyword: { color: '#5A9FC4' },
  function: { color: '#B69BD6' },
  'class-name': { color: '#B69BD6' },
  regex: { color: '#D6CCB4' },
  important: { color: '#C9534A', fontWeight: 'bold' },
};

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className="w-6 h-6 flex items-center justify-center rounded-sm text-text-quaternary hover:text-text-secondary hover:bg-hover transition-colors"
      style={{ transitionDuration: 'var(--duration-instant)' }}
    >
      {copied ? <Check className="w-3 h-3 text-green" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

function CodeBlock({ className, children }: { className?: string; children: string }) {
  const match = /language-(\w+)/.exec(className || '');
  const lang = match ? match[1] : '';
  const code = String(children).replace(/\n$/, '');

  return (
    <div className="my-2 rounded-lg border border-border-secondary overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 bg-bg-tertiary border-b border-border">
        <span className="text-[10px] font-[510] uppercase tracking-[0.04em] text-text-quaternary">
          {lang || 'code'}
        </span>
        <CopyButton text={code} />
      </div>
      <SyntaxHighlighter
        style={aosCodeTheme}
        language={lang || 'text'}
        PreTag="div"
        customStyle={{
          background: 'var(--color-bg)',
          margin: 0,
          maxHeight: '400px',
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

export function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...props }) {
          const isBlock = className?.includes('language-') || String(children).includes('\n');
          if (isBlock) {
            return <CodeBlock className={className}>{String(children)}</CodeBlock>;
          }
          return (
            <code className="font-mono text-[12px] bg-bg-tertiary text-accent px-1.5 py-0.5 rounded-[4px] border border-border" {...props}>
              {children}
            </code>
          );
        },
        p({ children }) {
          return <p className="text-[13px] text-text-secondary leading-[1.6] mb-2 last:mb-0">{children}</p>;
        },
        h1({ children }) {
          return <h3 className="text-[14px] font-[620] text-text mb-1 mt-3">{children}</h3>;
        },
        h2({ children }) {
          return <h3 className="text-[14px] font-[620] text-text mb-1 mt-3">{children}</h3>;
        },
        h3({ children }) {
          return <h3 className="text-[13px] font-[600] text-text mb-1 mt-2">{children}</h3>;
        },
        strong({ children }) {
          return <strong className="font-[600] text-text">{children}</strong>;
        },
        em({ children }) {
          return <em className="italic text-text-tertiary">{children}</em>;
        },
        ul({ children }) {
          return <ul className="ml-4 mb-2 list-disc text-text-secondary text-[13px] leading-[1.6] space-y-0.5">{children}</ul>;
        },
        ol({ children }) {
          return <ol className="ml-4 mb-2 list-decimal text-text-secondary text-[13px] leading-[1.6] space-y-0.5">{children}</ol>;
        },
        li({ children }) {
          return <li className="text-[13px] text-text-secondary">{children}</li>;
        },
        a({ href, children }) {
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline decoration-accent/30 hover:decoration-accent transition-colors">
              {children}
            </a>
          );
        },
        blockquote({ children }) {
          return <blockquote className="border-l-2 border-accent/30 pl-3 my-2 text-text-tertiary italic">{children}</blockquote>;
        },
        hr() {
          return <hr className="border-t border-border my-3" />;
        },
        table({ children }) {
          return (
            <div className="overflow-x-auto my-2">
              <table className="w-full text-[12px] font-mono border-collapse">{children}</table>
            </div>
          );
        },
        thead({ children }) { return <thead>{children}</thead>; },
        tbody({ children }) { return <tbody>{children}</tbody>; },
        tr({ children }) { return <tr className="border-b border-border">{children}</tr>; },
        th({ children }) {
          return <th className="text-left text-[10px] font-[590] uppercase tracking-[0.04em] text-text-quaternary px-3 py-1.5 border-b border-border-tertiary">{children}</th>;
        },
        td({ children }) {
          return <td className="text-[12px] text-text-secondary px-3 py-1.5 align-top">{children}</td>;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
