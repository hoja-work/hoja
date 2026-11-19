/* The agent's open question, answered where it is shown (HOJA-602 e2e loop).
 *
 * A run on the `hoja-coder` harness asks through Pydantic AI Harness's `AskUser`: one to ten
 * multiple-choice questions. The session is `awaiting_input` until a person answers here; the
 * answer goes back to the live run on its next heartbeat (`agentSessionPrompt(response:)`). An
 * elicitation without structured questions (an older runner) still gets a free-text answer. */
import { useState } from "react";

import type { AgentAnswer, AgentAsk } from "./types";

const FIELD =
  "border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 w-full min-w-0 border-solid rounded-md px-2 py-1.5 text-sm";
const PRIMARY =
  "inline-flex items-center rounded-md border border-line bg-accent px-3 py-1.5 text-sm text-foreground disabled:bg-panel disabled:text-muted";
const QUIET =
  "inline-flex items-center rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-muted hover:bg-subtle disabled:opacity-50";

export function AskCard({
  ask,
  prompt,
  busy,
  onAnswer,
}: {
  ask: AgentAsk | null;
  /** The elicitation's own text, shown when there are no structured questions. */
  prompt: string;
  busy: boolean;
  onAnswer: (body: string, response?: AgentAnswer) => Promise<void>;
}) {
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [custom, setCustom] = useState<Record<string, string>>({});
  const [text, setText] = useState("");

  const questions = ask?.questions ?? [];
  const answered = (header: string) =>
    (picked[header]?.length ?? 0) > 0 || (custom[header] ?? "").trim() !== "";
  const ready = questions.length
    ? questions.every((q) => answered(q.header))
    : text.trim() !== "";

  const toggle = (header: string, label: string, multi: boolean) =>
    setPicked((current) => {
      const now = current[header] ?? [];
      if (!multi) return { ...current, [header]: [label] };
      return {
        ...current,
        [header]: now.includes(label)
          ? now.filter((x) => x !== label)
          : [...now, label],
      };
    });

  const submit = async () => {
    if (!questions.length) {
      await onAnswer(text.trim());
      return;
    }
    const answers = questions.map((q) => ({
      header: q.header,
      selected: picked[q.header] ?? [],
      customAnswer: (custom[q.header] ?? "").trim() || undefined,
    }));
    const body = answers
      .map(
        (a) =>
          `${a.header}: ${[...a.selected, a.customAnswer].filter(Boolean).join(", ")}`,
      )
      .join("\n");
    await onAnswer(body, { answers });
  };

  return (
    <form
      className="border border-line rounded-md p-3 mb-3 flex flex-col gap-3 bg-panel"
      aria-label="The agent is asking"
      data-style-hooks="agent-ask"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready && !busy) void submit();
      }}
    >
      <b className="text-sm">The agent is waiting for your answer</b>
      {questions.length === 0 ? (
        <>
          <p className="text-sm text-muted m-0">{prompt}</p>
          <textarea
            className={FIELD}
            rows={3}
            aria-label="Your answer"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </>
      ) : (
        questions.map((q) => (
          <fieldset
            key={q.header}
            className="border-0 p-0 m-0 flex flex-col gap-1.5"
          >
            <legend className="text-sm text-foreground mb-1.5">
              <span className="text-muted">{q.header} · </span>
              {q.question}
            </legend>
            {q.options.map((o) => (
              <label
                key={o.label}
                className="flex items-start gap-2 text-sm cursor-pointer"
              >
                <input
                  type={q.multiSelect ? "checkbox" : "radio"}
                  name={`ask-${ask?.askId}-${q.header}`}
                  checked={(picked[q.header] ?? []).includes(o.label)}
                  onChange={() => toggle(q.header, o.label, q.multiSelect)}
                />
                <span>
                  {o.label}
                  {o.description ? (
                    <span className="text-muted"> — {o.description}</span>
                  ) : null}
                </span>
              </label>
            ))}
            <input
              className={FIELD}
              placeholder="Or answer in your own words"
              aria-label={`${q.header}: your own answer`}
              value={custom[q.header] ?? ""}
              onChange={(e) =>
                setCustom((c) => ({ ...c, [q.header]: e.target.value }))
              }
            />
          </fieldset>
        ))
      )}
      <div className="flex gap-2">
        <button type="submit" className={PRIMARY} disabled={!ready || busy}>
          {busy ? "Sending…" : "Send answer"}
        </button>
        {questions.length > 0 && (
          <button
            type="button"
            className={QUIET}
            disabled={busy}
            onClick={() =>
              void onAnswer("Declined to answer.", { cancelled: true })
            }
          >
            Decline
          </button>
        )}
      </div>
    </form>
  );
}
