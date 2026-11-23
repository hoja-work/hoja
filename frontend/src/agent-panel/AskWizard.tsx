import { useState } from "react";
import { ArrowLeft, ArrowRight, Check, SendHorizontal } from "lucide-react";
import {
  answerText,
  EMPTY_ANSWER,
  type AskAnswer,
  type AskQuestion,
} from "./types";

/**
 * Interactive step-by-step wizard rendered in place of the composer when the
 * agent's latest reply asks structured clarifying questions via `ask_user`.
 */
export function AskWizard({
  questions,
  onSubmit,
  disabled = false,
  inline = false,
}: {
  questions: AskQuestion[];
  onSubmit: (answer: string) => void;
  disabled?: boolean;
  /** Rendered inside the conversation scroll (phones): no height cap, no scroller of its own. */
  inline?: boolean;
}) {
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<AskAnswer[]>(() =>
    questions.map(() => ({ ...EMPTY_ANSWER })),
  );

  const q = questions[step];
  const a = answers[step];
  const options = q.options ?? [];
  const allowOther = options.length === 0 || q.allow_other !== false;
  const last = step === questions.length - 1;
  const answered = answerText(q, a) !== "";

  const update = (patch: Partial<AskAnswer>) =>
    setAnswers((prev) =>
      prev.map((x, j) => (j === step ? { ...x, ...patch } : x)),
    );

  const toggle = (label: string) => {
    if (q.multiple) {
      update({
        picked: a.picked.includes(label)
          ? a.picked.filter((x) => x !== label)
          : [...a.picked, label],
      });
    } else {
      update({ picked: [label], otherOn: false });
    }
  };

  const next = () => {
    if (!answered || disabled) return;
    if (!last) {
      setStep(step + 1);
      return;
    }
    const body =
      questions.length === 1
        ? answerText(q, a)
        : questions
            .map(
              (x, i) =>
                `${x.header || x.question}: ${answerText(x, answers[i])}`,
            )
            .join("\n");
    onSubmit(body);
  };

  return (
    <form
      className={`mx-auto flex w-full max-w-3xl flex-col gap-3 rounded-2xl border border-line bg-panel p-4 shadow-sm ${
        inline ? "" : "max-h-96 overflow-y-auto"
      }`}
      aria-label="Agent question"
      data-ask-wizard=""
      onSubmit={(e) => {
        e.preventDefault();
        next();
      }}
    >
      {(q.header || questions.length > 1) && (
        <div className="flex items-center justify-between gap-2">
          {q.header && (
            <span className="rounded-full bg-subtle px-2.5 py-0.5 text-xs font-medium text-muted">
              {q.header}
            </span>
          )}
          {questions.length > 1 && (
            <div className="ml-auto flex items-center gap-2">
              <div className="flex items-center gap-1" aria-hidden="true">
                {questions.map((_, i) => (
                  <span
                    key={i}
                    className={`h-1.5 rounded-full transition-colors ${
                      i === step
                        ? "w-4 bg-foreground"
                        : i < step
                          ? "w-1.5 bg-foreground/50"
                          : "w-1.5 bg-line"
                    }`}
                  />
                ))}
              </div>
              <span className="text-xs text-muted tabular-nums">
                {step + 1} of {questions.length}
              </span>
            </div>
          )}
        </div>
      )}

      <fieldset key={step} className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-1 flex flex-col gap-0.5 p-0">
          <span className="text-sm font-semibold text-foreground">
            {q.question}
          </span>
          {q.multiple && (
            <span className="text-xs text-muted">Select all that apply</span>
          )}
        </legend>

        <div className="flex flex-col gap-1.5">
          {options.map((o) => {
            const selected = a.picked.includes(o.label);
            return (
              <label
                key={o.label}
                className={`flex cursor-pointer items-start gap-2.5 rounded-xl border p-2.5 text-sm transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none shell-phone:min-h-11 ${
                  selected
                    ? "border-foreground bg-subtle/70"
                    : "border-line bg-bg/40"
                }`}
              >
                <input
                  className="sr-only"
                  type={q.multiple ? "checkbox" : "radio"}
                  name={`ask-${step}`}
                  checked={selected}
                  onChange={() => toggle(o.label)}
                />
                <span
                  aria-hidden="true"
                  className={`mt-0.5 flex size-4 shrink-0 items-center justify-center border ${
                    q.multiple ? "rounded" : "rounded-full"
                  } ${
                    selected
                      ? "border-foreground bg-foreground text-bg"
                      : "border-line bg-panel"
                  }`}
                >
                  {selected && <Check className="size-3" />}
                </span>
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="font-medium text-foreground">{o.label}</span>
                  {o.description && (
                    <span className="text-xs text-muted">{o.description}</span>
                  )}
                </span>
              </label>
            );
          })}

          {allowOther && options.length > 0 && (
            <label
              className={`flex cursor-pointer items-start gap-2.5 rounded-xl border p-2.5 text-sm transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none shell-phone:min-h-11 ${
                a.otherOn
                  ? "border-foreground bg-subtle/70"
                  : "border-line bg-bg/40"
              }`}
            >
              <input
                className="sr-only"
                type={q.multiple ? "checkbox" : "radio"}
                name={`ask-${step}`}
                checked={a.otherOn}
                onChange={() =>
                  update(
                    q.multiple
                      ? { otherOn: !a.otherOn }
                      : { otherOn: true, picked: [] },
                  )
                }
              />
              <span
                aria-hidden="true"
                className={`mt-0.5 flex size-4 shrink-0 items-center justify-center border ${
                  q.multiple ? "rounded" : "rounded-full"
                } ${
                  a.otherOn
                    ? "border-foreground bg-foreground text-bg"
                    : "border-line bg-panel"
                }`}
              >
                {a.otherOn && <Check className="size-3" />}
              </span>
              <span className="font-medium text-foreground">Other</span>
            </label>
          )}
        </div>

        {allowOther && (options.length === 0 || a.otherOn) && (
          <textarea
            className="w-full resize-y rounded-xl border border-line bg-bg px-3 py-2 text-sm text-foreground placeholder:text-muted focus:border-foreground/40 focus:outline-none shell-phone:min-h-11"
            rows={2}
            placeholder="Write your answer…"
            aria-label="Your answer"
            value={a.other}
            onChange={(e) => update({ other: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                next();
              }
            }}
          />
        )}

        {options.length > 0 && (
          <input
            className="w-full rounded-xl border border-line bg-bg px-3 py-2 text-sm text-foreground placeholder:text-muted focus:border-foreground/40 focus:outline-none shell-phone:min-h-11"
            type="text"
            placeholder="Add details (optional)"
            aria-label="Additional details"
            value={a.note}
            onChange={(e) => update({ note: e.target.value })}
          />
        )}
      </fieldset>

      <div className="flex items-center justify-end gap-2">
        {step > 0 && (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm font-medium text-foreground transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none shell-phone:min-h-11"
            onClick={() => setStep(step - 1)}
          >
            <ArrowLeft className="size-3.5" aria-hidden="true" />
            <span>Back</span>
          </button>
        )}
        <button
          type="submit"
          className="inline-flex items-center gap-1.5 rounded-lg bg-foreground px-3.5 py-1.5 text-sm font-medium text-bg transition-colors duration-150 ease-out hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 shell-phone:min-h-11"
          disabled={!answered || disabled}
        >
          <span>{last ? "Send answer" : "Next"}</span>
          {last ? (
            <SendHorizontal className="size-3.5" aria-hidden="true" />
          ) : (
            <ArrowRight className="size-3.5" aria-hidden="true" />
          )}
        </button>
      </div>
    </form>
  );
}
