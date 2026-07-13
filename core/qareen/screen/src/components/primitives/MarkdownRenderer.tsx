import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import type { Components } from 'react-markdown';

interface MarkdownRendererProps {
  content: string;
  className?: string;
  /** If true, renders headings one level smaller (for nested contexts like cards) */
  compact?: boolean;
}

const headingStyles = {
  h1: 'text-[24px] font-[700] text-text tracking-[-0.02em] mt-10 mb-4 pb-3 border-b border-border',
  h2: 'text-[19px] font-[650] text-text tracking-[-0.015em] mt-8 mb-3',
  h3: 'text-[16px] font-[600] text-text tracking-[-0.01em] mt-6 mb-2',
  h4: 'text-[14px] font-[600] text-text mt-4 mb-1.5',
};

function buildComponents(compact: boolean): Components {
  const h1Style = compact ? headingStyles.h2 : headingStyles.h1;
  const h2Style = compact ? headingStyles.h3 : headingStyles.h2;
  const h3Style = compact ? headingStyles.h4 : headingStyles.h3;

  return {
    h1: ({ children }) => <h1 className={h1Style}>{children}</h1>,
    h2: ({ children }) => <h2 className={h2Style}>{children}</h2>,
    h3: ({ children }) => <h3 className={h3Style}>{children}</h3>,
    p: ({ children }) => <p className="text-[15px] leading-[1.75] text-text-secondary mb-4">{children}</p>,
    li: ({ children }) => <li className="text-[15px] leading-[1.75] text-text-secondary mb-1">{children}</li>,
    ul: ({ children }) => <ul className="list-disc pl-5 mb-4 space-y-0.5">{children}</ul>,
    ol: ({ children }) => <ol className="list-decimal pl-5 mb-4 space-y-0.5">{children}</ol>,
    a: ({ href, children }) => (
      <a
        href={href}
        className="text-accent hover:text-accent-hover underline underline-offset-2 decoration-accent/30 transition-colors cursor-pointer"
        style={{ transitionDuration: '80ms' }}
      >
        {children}
      </a>
    ),
    blockquote: ({ children }) => (
      <blockquote className="border-l-[3px] border-accent/30 pl-4 my-4 text-text-tertiary italic">
        {children}
      </blockquote>
    ),
    strong: ({ children }) => <strong className="font-[600] text-text">{children}</strong>,
    hr: () => <hr className="border-border my-8" />,
    table: ({ children }) => (
      <div className="overflow-x-auto my-4 rounded-[7px] border border-border">
        <table className="w-full text-[13px]">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-bg-tertiary">{children}</thead>,
    th: ({ children }) => (
      <th className="px-3 py-2 text-left font-[600] text-text-secondary border-b border-border text-[12px]">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="px-3 py-2 text-text-secondary border-b border-border/50 text-[13px]">
        {children}
      </td>
    ),
    code: ({ className, children }) => {
      const match = /language-(\w+)/.exec(className || '');
      if (!match) {
        return (
          <code className="text-[13px] bg-bg-tertiary text-accent px-1.5 py-0.5 rounded-[4px] font-mono">
            {children}
          </code>
        );
      }
      return (
        <SyntaxHighlighter
          style={oneDark}
          language={match[1]}
          PreTag="div"
          customStyle={{
            background: 'var(--color-bg-tertiary, #2A2520)',
            padding: '16px',
            borderRadius: '7px',
            fontSize: '13px',
            margin: '16px 0',
            border: '1px solid rgba(255, 245, 235, 0.06)',
          }}
        >
          {String(children).replace(/\n$/, '')}
        </SyntaxHighlighter>
      );
    },
    pre: ({ children }) => <>{children}</>,
    img: ({ src, alt }) => (
      <figure className="my-4">
        <img
          src={src}
          alt={alt || ''}
          loading="lazy"
          className="rounded-[7px] border border-border max-w-full"
        />
        {alt && (
          <figcaption className="text-[12px] text-text-tertiary mt-2 text-center italic">
            {alt}
          </figcaption>
        )}
      </figure>
    ),
  };
}

const defaultComponents = buildComponents(false);
const compactComponents = buildComponents(true);

export function MarkdownRenderer({ content, className, compact = false }: MarkdownRendererProps) {
  const components = compact ? compactComponents : defaultComponents;

  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
