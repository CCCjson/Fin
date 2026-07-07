import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

/* ================================================================
   统一的暗色主题 Markdown 渲染组件
   （抽自 Advisor/Reports/News，消除重复的 mdComponents 定义）
   ================================================================ */
export const mdComponents: Components = {
  h1: ({ children }) => (
    <h1 className="text-2xl font-bold text-white mt-6 mb-3 pb-2 border-b border-border-light">{children}</h1>
  ),
  h2: ({ children }) => (
    <div className="mt-6 mb-2">
      <h2 className="text-xl font-bold text-white flex items-center gap-3">
        <span className="w-1 h-6 bg-gradient-to-b from-violet-500 to-purple-600 rounded-full" />
        {children}
      </h2>
      <div className="mt-1.5 h-px bg-gradient-to-r from-border-light to-transparent" />
    </div>
  ),
  h3: ({ children }) => (
    <h3 className="text-lg font-semibold text-primary-light mt-4 mb-2 flex items-center gap-2">
      <span className="w-1.5 h-1.5 bg-primary-light rounded-full" />
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-base font-semibold text-gray-200 mt-3 mb-1.5">{children}</h4>
  ),
  p: ({ children }) => <p className="text-gray-300 leading-6 mb-2">{children}</p>,
  strong: ({ children }) => <strong className="text-white font-semibold">{children}</strong>,
  em: ({ children }) => <em className="text-accent-cyan not-italic font-medium">{children}</em>,
  ul: ({ children }) => <ul className="space-y-1 mb-2 ml-1">{children}</ul>,
  ol: ({ children }) => <ol className="space-y-1 mb-2 ml-1 list-decimal list-inside">{children}</ol>,
  li: ({ children }) => (
    <li className="text-gray-300 leading-6 flex items-start gap-2">
      <span className="mt-2 w-1.5 h-1.5 bg-accent-purple rounded-full shrink-0" />
      <span className="flex-1">{children}</span>
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-accent-purple/50 bg-accent-purple/5 pl-3 py-1 my-2 rounded-r-lg">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = className?.includes('language-');
    if (isBlock) {
      return (
        <code className="block bg-dark text-sm text-gray-300 p-3 rounded-lg overflow-x-auto border border-border my-3 font-mono">
          {children}
        </code>
      );
    }
    return (
      <code className="bg-violet-500/15 text-violet-300 px-1.5 py-0.5 rounded text-sm font-mono">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="bg-dark rounded-xl border border-border overflow-hidden my-3">{children}</pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-3 rounded-xl border border-border">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-dark-light border-b border-border">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-border">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-dark-light/50 transition-colors">{children}</tr>,
  th: ({ children }) => (
    <th className="px-3 py-1.5 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">{children}</th>
  ),
  td: ({ children }) => <td className="px-3 py-1.5 text-gray-300">{children}</td>,
  hr: () => (
    <hr className="my-4 border-none h-px bg-gradient-to-r from-transparent via-border-light to-transparent" />
  ),
  a: ({ href, children }) => (
    <a href={href} className="text-primary-light hover:text-primary underline underline-offset-2 transition-colors">
      {children}
    </a>
  ),
};

// memo：文本未变化时跳过整棵 markdown AST 的重新解析（长会话里全量重渲染的大头）
export const MarkdownView: React.FC<{ children: string }> = React.memo(({ children }) => (
  <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
    {children}
  </ReactMarkdown>
));

export default MarkdownView;
