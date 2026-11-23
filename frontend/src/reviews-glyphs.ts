/* ---- Reviews state glyphs: the reference's own vectors -----------------------
 *
 * HOJA-417. The previous glyph was a hand-drawn outline (one stroked path, 12px, inset
 * 12px); Linear draws a filled 14px icon whose paths differ per state. These path strings
 * are copied verbatim from the reference DOM (`svg path[d]`, `fill-rule="evenodd"`,
 * `viewBox="0 0 16 16"`) captured at 1440x900 by scripts/ui-probes/reviews-parity.mjs:
 *
 *   open / ready : 4 paths, fill #43bc58   (lch(68% 64.37 141.95) resolved through canvas)
 *   draft        : 5 paths, fill #5c5c5e   (lch(39.176% 1.25 282)  resolved through canvas)
 *
 * The draft icon keeps the branch and node of the open icon and replaces the merge head
 * with two X strokes, which is why the last two paths are shared. Reproduced rather than
 * re-drawn so the shape, the fill and the 14px box are the reference's and not an
 * approximation of it.
 */
export const REVIEW_GLYPH_PULL: readonly string[] = [
  'M12.5 10C13.8807 10 15 11.1193 15 12.5C15 13.8807 13.8807 15 12.5 15C11.1193 15 10 13.8807 10 12.5C10 11.1193 11.1193 10 12.5 10ZM12.5 11.5C11.9477 11.5 11.5 11.9477 11.5 12.5C11.5 13.0523 11.9477 13.5 12.5 13.5C13.0523 13.5 13.5 13.0523 13.5 12.5C13.5 11.9477 13.0523 11.5 12.5 11.5Z',
  'M3.5 4.5C3.91414 4.50009 4.25 4.83584 4.25 5.25V14.249C4.24982 14.663 3.91403 14.9989 3.5 14.999C3.0859 14.999 2.75018 14.6631 2.75 14.249V5.25C2.75 4.83579 3.08579 4.5 3.5 4.5Z',
  'M10 2.75C11.7949 2.75 13.25 4.20507 13.25 6V10.75C13.25 11.1642 12.9142 11.5 12.5 11.5C12.0858 11.5 11.75 11.1642 11.75 10.75V6C11.75 5.0335 10.9665 4.25 10 4.25H8C7.58579 4.25 7.25 3.91421 7.25 3.5C7.25 3.08579 7.58579 2.75 8 2.75H10Z',
  'M3.5 1C4.88071 1 6 2.11929 6 3.5C6 4.88071 4.88071 6 3.5 6C2.11929 6 1 4.88071 1 3.5C1 2.11929 2.11929 1 3.5 1ZM3.5 2.5C2.94772 2.5 2.5 2.94772 2.5 3.5C2.5 4.05228 2.94772 4.5 3.5 4.5C4.05228 4.5 4.5 4.05228 4.5 3.5C4.5 2.94772 4.05228 2.5 3.5 2.5Z',
];

export const REVIEW_GLYPH_DRAFT: readonly string[] = [
  'M3.5 4.5C3.08579 4.5 2.75 4.83579 2.75 5.25V14.249C2.75018 14.6631 3.0859 14.999 3.5 14.999C3.91403 14.9989 4.24982 14.663 4.25 14.249V5.25C4.25 4.83584 3.91414 4.50009 3.5 4.5Z',
  'M8.68066 10.2617C8.95025 9.94737 9.42384 9.91115 9.73828 10.1807C10.0526 10.4503 10.0889 10.9238 9.81934 11.2383L8.73828 12.5L9.81934 13.7617C10.0889 14.0762 10.0526 14.5497 9.73828 14.8193C9.42384 15.0888 8.95024 15.0526 8.68066 14.7383L7.18066 12.9883C6.93993 12.7074 6.93995 12.2926 7.18066 12.0117L8.68066 10.2617Z',
  'M12.2617 10.1807C12.5762 9.91115 13.0497 9.94737 13.3193 10.2617L14.8193 12.0117C15.06 12.2926 15.0601 12.7074 14.8193 12.9883L13.3193 14.7383C13.0498 15.0526 12.5762 15.0888 12.2617 14.8193C11.9474 14.5497 11.9111 14.0762 12.1807 13.7617L13.2617 12.5L12.1807 11.2383C11.9111 10.9238 11.9474 10.4503 12.2617 10.1807Z',
  'M10 2.75C11.7949 2.75 13.25 4.20507 13.25 6V7.75C13.25 8.16421 12.9142 8.5 12.5 8.5C12.0858 8.5 11.75 8.16421 11.75 7.75V6C11.75 5.0335 10.9665 4.25 10 4.25H8C7.58579 4.25 7.25 3.91421 7.25 3.5C7.25 3.08579 7.58579 2.75 8 2.75H10Z',
  'M3.5 1C4.88071 1 6 2.11929 6 3.5C6 4.88071 4.88071 6 3.5 6C2.11929 6 1 4.88071 1 3.5C1 2.11929 2.11929 1 3.5 1ZM3.5 2.5C2.94772 2.5 2.5 2.94772 2.5 3.5C2.5 4.05228 2.94772 4.5 3.5 4.5C4.05228 4.5 4.5 4.05228 4.5 3.5C4.5 2.94772 4.05228 2.5 3.5 2.5Z',
];

/** The 14px filled state glyph. `draft` is the only state the reviews queue renders with
 *  the muted icon; merged/closed rows never reach the ledger (see reviews-gate.ts). */
export function reviewGlyphPaths(state?: string | null): readonly string[] {
  return (state || '').toLowerCase() === 'draft' ? REVIEW_GLYPH_DRAFT : REVIEW_GLYPH_PULL;
}
