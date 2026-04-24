import { useEffect, useState } from 'react';
import { codeToHtml } from 'shiki';

interface HighlightedCodeProps {
  code: string;
  lang: 'python' | 'typescript' | 'bash' | 'yaml';
  className?: string;
}

/**
 * Thin Shiki wrapper. Produces two DOM trees: a plain <pre> fallback
 * while Shiki is loading / if highlighting fails, and a colourised
 * <div> with dangerously-set inner HTML once the highlighter resolves.
 * Shiki outputs inline styles, so no global CSS is needed — dark
 * and light both "just work".
 */
export default function HighlightedCode({ code, lang, className = '' }: HighlightedCodeProps) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    codeToHtml(code, {
      lang,
      // Dual-theme output: Shiki emits each token twice with
      // `--shiki-light` / `--shiki-dark` CSS variables. Our
      // `shiki-wrap` stylesheet picks whichever matches
      // `html.dark`, so the block reads correctly on both palettes.
      themes: {
        light: 'github-light',
        dark: 'github-dark-default',
      },
    })
      .then((h) => {
        if (!cancelled) setHtml(h);
      })
      .catch(() => {
        /* leave fallback pre visible */
      });
    return () => {
      cancelled = true;
    };
  }, [code, lang]);

  if (html) {
    return (
      <div
        className={`shiki-wrap rounded-xl overflow-x-auto text-sm ${className}`}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }
  // Fallback while shiki loads / on error — match the visual weight
  // of the highlighted output so the page doesn't shift.
  return (
    <pre
      className={`bg-surface-50 dark:bg-surface-900 rounded-xl px-4 py-3 font-mono text-sm text-zinc-600 dark:text-zinc-300 overflow-x-auto whitespace-pre ${className}`}
    >
      {code}
    </pre>
  );
}
