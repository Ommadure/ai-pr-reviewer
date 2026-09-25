/**
 * Render `code spans` in model-written text. Deliberately nothing else: no HTML,
 * no links, no images, so text shaped by PR content can't inject markup here.
 */
export function InlineCode({ text }: { text: string }) {
  const parts = text.split(/(`[^`\n]+`)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.length > 2 && part.startsWith("`") && part.endsWith("`") ? (
          <code key={i} className="rounded bg-gutter px-1 py-0.5 font-mono text-[0.85em] text-ink">
            {part.slice(1, -1)}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}
