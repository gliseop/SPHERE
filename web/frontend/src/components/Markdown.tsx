import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function Markdown({ content, className }: { content: string; className?: string }) {
  return (
    <div className={className ?? 'md'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div className="md-table-wrap">
              <table>{children}</table>
            </div>
          ),
          code: ({ className, children }) => (
            <code className={className}>{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="md-pre">{children}</pre>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
