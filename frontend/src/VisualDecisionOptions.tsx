import React, { useCallback, useEffect, useMemo, useState } from "react";
import type { DecisionOption } from "./decisions";

const VIDEO_EXT_RE = /\.(mp4|webm|mov|m4v|ogv)(?:$|[?#])/i;
const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|avif|bmp|svg)(?:$|[?#])/i;

export function resolveOptionMediaKind(
  option: DecisionOption,
): "image" | "video" | "link" | null {
  const url = option.mediaUrl?.trim();
  if (!url) return null;
  const declared = option.mediaType?.trim().toLowerCase();
  if (declared === "image" || declared === "video" || declared === "link") {
    return declared;
  }
  if (VIDEO_EXT_RE.test(url)) return "video";
  if (IMAGE_EXT_RE.test(url) || url.startsWith("/api/uploads/")) return "image";
  return "link";
}

export function ChosenDecisionMediaPreview({
  option,
}: {
  option?: DecisionOption | null;
}) {
  if (!option?.mediaUrl) return null;
  const kind = resolveOptionMediaKind(option);
  const url = option.mediaUrl;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="mt-1.5 inline-flex items-center gap-2 rounded-md border border-neutral-300/70 px-2 py-1 text-xs no-underline hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
      title={option.label}
    >
      {kind === "image" ? (
        <img
          src={url}
          alt={option.label}
          loading="lazy"
          className="h-8 w-12 rounded object-cover"
        />
      ) : null}
      <span>{option.label}</span>
    </a>
  );
}

export function VisualDecisionOptions({
  options,
  disabled = false,
  onPick,
}: {
  options: DecisionOption[];
  disabled?: boolean;
  onPick: (key: string) => void;
}) {
  const [previewKey, setPreviewKey] = useState<string | null>(null);

  const visualOptions = useMemo(
    () => options.filter((o) => Boolean(o.mediaUrl?.trim())),
    [options],
  );

  const activeIndex = useMemo(
    () =>
      previewKey
        ? visualOptions.findIndex((o) => o.key === previewKey)
        : -1,
    [previewKey, visualOptions],
  );
  const activeOption =
    activeIndex >= 0 ? visualOptions[activeIndex] : undefined;

  const stepPreview = useCallback(
    (delta: number) => {
      if (!visualOptions.length || activeIndex < 0) return;
      const nextIndex =
        (activeIndex + delta + visualOptions.length) % visualOptions.length;
      setPreviewKey(visualOptions[nextIndex].key);
    },
    [activeIndex, visualOptions],
  );

  useEffect(() => {
    if (!activeOption) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setPreviewKey(null);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        stepPreview(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        stepPreview(1);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeOption, stepPreview]);

  return (
    <>
      <div
        className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
        data-testid="visual-decision-grid"
      >
        {options.map((option) => {
          const kind = resolveOptionMediaKind(option);
          const mediaUrl = option.mediaUrl?.trim() || "";
          return (
            <div
              key={option.key}
              className="flex flex-col justify-between gap-2.5 rounded-lg border border-neutral-300/80 bg-white/80 p-3 dark:border-neutral-700 dark:bg-neutral-900/70"
              data-testid={`visual-decision-card-${option.key}`}
            >
              <div className="flex flex-col gap-2">
                {kind === "image" && mediaUrl ? (
                  <button
                    type="button"
                    onClick={() => setPreviewKey(option.key)}
                    className="group relative overflow-hidden rounded-md border border-neutral-200 bg-neutral-100 p-0 text-left dark:border-neutral-800 dark:bg-neutral-950"
                    title={`Preview ${option.label}`}
                  >
                    <img
                      src={mediaUrl}
                      alt={option.label}
                      loading="lazy"
                      className="h-40 w-full object-cover"
                    />
                  </button>
                ) : kind === "video" && mediaUrl ? (
                  <div className="overflow-hidden rounded-md border border-neutral-200 bg-neutral-950 dark:border-neutral-800">
                    <video
                      src={mediaUrl}
                      controls
                      muted
                      playsInline
                      className="h-40 w-full object-cover"
                    />
                  </div>
                ) : kind === "link" && mediaUrl ? (
                  <a
                    href={mediaUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="flex h-24 items-center justify-between gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-3 text-sm underline-offset-2 hover:underline dark:border-neutral-800 dark:bg-neutral-950"
                  >
                    <span className="truncate">{mediaUrl}</span>
                    <span className="shrink-0 text-xs text-neutral-500">
                      Open link
                    </span>
                  </a>
                ) : null}

                <div className="flex flex-col gap-1">
                  <span className="text-sm font-semibold">{option.label}</span>
                  {option.description ? (
                    <span className="text-xs text-neutral-600 dark:text-neutral-400">
                      {option.description}
                    </span>
                  ) : null}
                </div>
              </div>

              <div className="flex items-center justify-end gap-2 pt-1">
                {mediaUrl ? (
                  <button
                    type="button"
                    onClick={() => setPreviewKey(option.key)}
                    className="rounded-md border border-neutral-300 bg-transparent px-2.5 py-1.5 text-xs font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                  >
                    Preview
                  </button>
                ) : null}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onPick(option.key)}
                  className="rounded-md bg-neutral-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-neutral-700 disabled:cursor-default disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
                >
                  Select
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {activeOption && activeOption.mediaUrl ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Preview ${activeOption.label}`}
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
        >
          <button
            type="button"
            aria-label="Close preview"
            className="fixed inset-0 border-0 bg-black/75"
            onClick={() => setPreviewKey(null)}
          />
          <div className="relative z-10 flex max-h-full w-full max-w-4xl flex-col gap-3 overflow-auto rounded-xl border border-neutral-300/70 bg-white p-4 shadow-xl dark:border-neutral-700 dark:bg-neutral-900">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-col">
                <span className="text-base font-semibold">
                  {activeOption.label}
                </span>
                {activeOption.description ? (
                  <span className="text-xs text-neutral-600 dark:text-neutral-400">
                    {activeOption.description}
                  </span>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                {visualOptions.length > 1 ? (
                  <>
                    <button
                      type="button"
                      onClick={() => stepPreview(-1)}
                      className="rounded-md border border-neutral-300 px-2.5 py-1 text-xs font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                    >
                      Prev
                    </button>
                    <span className="text-xs text-neutral-500">
                      {activeIndex + 1} / {visualOptions.length}
                    </span>
                    <button
                      type="button"
                      onClick={() => stepPreview(1)}
                      className="rounded-md border border-neutral-300 px-2.5 py-1 text-xs font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                    >
                      Next
                    </button>
                  </>
                ) : null}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    const chosen = activeOption.key;
                    setPreviewKey(null);
                    onPick(chosen);
                  }}
                  className="rounded-md bg-neutral-900 px-3 py-1 text-xs font-semibold text-white hover:bg-neutral-700 disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
                >
                  Select
                </button>
                <button
                  type="button"
                  onClick={() => setPreviewKey(null)}
                  className="rounded-md border border-neutral-300 px-2.5 py-1 text-xs font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                >
                  Close
                </button>
              </div>
            </div>

            <div className="flex items-center justify-center rounded-lg bg-neutral-100 p-2 dark:bg-neutral-950">
              {resolveOptionMediaKind(activeOption) === "video" ? (
                <video
                  src={activeOption.mediaUrl}
                  controls
                  autoPlay
                  muted
                  playsInline
                  className="max-h-96 w-full rounded object-contain"
                />
              ) : resolveOptionMediaKind(activeOption) === "link" ? (
                <a
                  href={activeOption.mediaUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="p-4 text-sm underline"
                >
                  {activeOption.mediaUrl}
                </a>
              ) : (
                <img
                  src={activeOption.mediaUrl}
                  alt={activeOption.label}
                  className="max-h-96 w-full rounded object-contain"
                />
              )}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
