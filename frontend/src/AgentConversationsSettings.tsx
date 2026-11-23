import React, { useEffect, useState } from "react";

type Conversation = {
  id: string;
  messageCount: number;
  bytes: number;
  updatedAt: string;
};
type Summary = {
  count: number;
  totalBytes: number;
  conversations: Conversation[];
};

const QUERY =
  "query AgentConversations { agentConversations { count totalBytes conversations { id messageCount bytes updatedAt } } }";

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function AgentConversationsSettings() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    const token = localStorage.getItem("hoja_token");
    fetch("/graphql", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(token && token !== "session"
          ? { authorization: `Bearer ${token}` }
          : {}),
      },
      credentials: "include",
      body: JSON.stringify({ query: QUERY }),
    })
      .then((r) => r.json())
      .then((body) => {
        if (!live) return;
        if (body.errors?.length) throw new Error(body.errors[0].message);
        setSummary(body.data.agentConversations);
      })
      .catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, []);

  const heading = (
    <header className="mt-0 mr-0 mb-8 ml-0 [@media_(max-width:_700px)]:mb-6.75 pb-0 border-b-0 [@media_(max-width:_700px)]:pb-5">
      <h1 className="text-(length:--ds-settings-title-size) tracking-[-.035em] mt-0 mr-0 mb-2 ml-0 font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) text-(--settings-ink) font-(family-name:--hoja-font-ui) [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
        Agent conversations
      </h1>
      <p className="text-(--settings-muted) text-[13px] leading-[1.6] wrap-anywhere max-w-full mt-0 mr-0 mb-0 ml-0 [@media_(max-width:_700px)]:text-[13px]">
        Review the conversation history stored for your agent, including message
        count and local storage use.
      </p>
    </header>
  );

  if (error)
    return (
      <section
        data-settings-page=""
        className="w-[min(100%,var(--ds-settings-column-width))] mt-0 mr-auto mb-0 ml-auto pt-(--ds-settings-column-padding) pr-(--ds-settings-column-padding) pb-16 pl-(--ds-settings-column-padding) font-(family-name:--hoja-font-ui) [font-optical-sizing:auto] min-w-0 shell-phone:pt-0 text-(--settings-ink) max-w-(--ds-settings-column-width) [@media_(max-width:_700px)]:pt-0 [html[data-theme=dark]_&]:bg-transparent [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) [--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-on-accent:#fff] [--settings-danger:#242424] [--settings-success:#242424] [html[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [html[data-theme=dark]_&]:[--settings-on-accent:#171717] [html[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [html[data-theme=dark]_&]:[--settings-muted:#aaa] [html[data-theme=dark]_&]:[--settings-line:#353535] [html[data-theme=dark]_&]:[--settings-card:#1b1b1b] [html[data-theme=dark]_&]:[--settings-subtle:#262626] [html[data-theme=dark]_&]:[--settings-danger:#eee] [html[data-theme=dark]_&]:[--settings-success:#d8d8d8]"
      >
        {heading}
        <div
          className="grid justify-items-center text-center text-sm mt-0 mr-0 mb-9 ml-0 pt-7.5! pr-4.5! pb-7.5! pl-4.5! border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) gap-1.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="alert"
        >
          <b className="font-semibold text-(--settings-ink) text-[12px]">
            Could not load conversations
          </b>
          <p className="text-[11px] leading-[1.55] text-muted max-w-90 mt-0 mr-0 mb-0 ml-0">
            {error}
          </p>
        </div>
      </section>
    );
  if (!summary)
    return (
      <section
        data-settings-page=""
        className="w-[min(100%,var(--ds-settings-column-width))] mt-0 mr-auto mb-0 ml-auto pt-(--ds-settings-column-padding) pr-(--ds-settings-column-padding) pb-16 pl-(--ds-settings-column-padding) font-(family-name:--hoja-font-ui) [font-optical-sizing:auto] min-w-0 shell-phone:pt-0 text-(--settings-ink) max-w-(--ds-settings-column-width) [@media_(max-width:_700px)]:pt-0 [html[data-theme=dark]_&]:bg-transparent [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) [--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-on-accent:#fff] [--settings-danger:#242424] [--settings-success:#242424] [html[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [html[data-theme=dark]_&]:[--settings-on-accent:#171717] [html[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [html[data-theme=dark]_&]:[--settings-muted:#aaa] [html[data-theme=dark]_&]:[--settings-line:#353535] [html[data-theme=dark]_&]:[--settings-card:#1b1b1b] [html[data-theme=dark]_&]:[--settings-subtle:#262626] [html[data-theme=dark]_&]:[--settings-danger:#eee] [html[data-theme=dark]_&]:[--settings-success:#d8d8d8]"
      >
        {heading}
        <div
          className="grid justify-items-center text-center text-sm mt-0 mr-0 mb-9 ml-0 pt-7.5! pr-4.5! pb-7.5! pl-4.5! border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) gap-1.75 [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          role="status"
        >
          Loading conversation storage…
        </div>
      </section>
    );

  return (
    <section
      data-settings-page=""
      className="w-[min(100%,var(--ds-settings-column-width))] mt-0 mr-auto mb-0 ml-auto pt-(--ds-settings-column-padding) pr-(--ds-settings-column-padding) pb-16 pl-(--ds-settings-column-padding) font-(family-name:--hoja-font-ui) [font-optical-sizing:auto] min-w-0 shell-phone:pt-0 text-(--settings-ink) max-w-(--ds-settings-column-width) [@media_(max-width:_700px)]:pt-0 [html[data-theme=dark]_&]:bg-transparent [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) [--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-on-accent:#fff] [--settings-danger:#242424] [--settings-success:#242424] [html[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [html[data-theme=dark]_&]:[--settings-on-accent:#171717] [html[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [html[data-theme=dark]_&]:[--settings-muted:#aaa] [html[data-theme=dark]_&]:[--settings-line:#353535] [html[data-theme=dark]_&]:[--settings-card:#1b1b1b] [html[data-theme=dark]_&]:[--settings-subtle:#262626] [html[data-theme=dark]_&]:[--settings-danger:#eee] [html[data-theme=dark]_&]:[--settings-success:#d8d8d8]"
      aria-label="Agent conversations"
    >
      <header className="mt-0 mr-0 mb-8 ml-0 [@media_(max-width:_700px)]:mb-6.75 pb-0 border-b-0 [@media_(max-width:_700px)]:pb-5">
        <h1 className="text-(length:--ds-settings-title-size) tracking-[-.035em] mt-0 mr-0 mb-2 ml-0 font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) text-(--settings-ink) font-(family-name:--hoja-font-ui) [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Agent conversations
        </h1>
        <p className="text-(--settings-muted) text-[13px] leading-[1.6] wrap-anywhere max-w-full mt-0 mr-0 mb-0 ml-0 [@media_(max-width:_700px)]:text-[13px]">
          Stored agent history for this account. Review activity and storage
          without exposing conversation content.
        </p>
      </header>
      <div
        className="grid border-y border-line grid-cols-2 gap-0 mb-8.5 [@media_(max-width:_700px)]:gap-2.5 [@media_(max-width:_390px)]:grid-cols-[minmax(0,1fr)] border-t border-t-(--settings-line) border-b border-b-(--settings-line)"
        aria-label="Conversation storage summary"
      >
        <div className="grid gap-1.5 pt-6 pr-7 pb-6 pl-0 border-0 rounded-none bg-transparent [@media_(max-width:_700px)]:pt-4 [@media_(max-width:_700px)]:pr-4 [@media_(max-width:_700px)]:pb-4 [@media_(max-width:_700px)]:pl-4">
          <span className="text-(--settings-muted) text-[12px]">
            Saved conversations
          </span>
          <b className="text-(--settings-accent-strong) text-[44px] font-medium tracking-[-.035em] [@media_(max-width:_700px)]:text-[22px] font-(family-name:--hoja-font-ui)">
            {summary.count}
          </b>
          <small className="leading-relaxed text-(--settings-muted) text-[12px]">
            Across your agent history
          </small>
        </div>
        <div className="grid gap-1.5 pt-6 pr-7 pb-6 pl-7 border-0 border-l border-l-(--settings-line) rounded-none bg-transparent [@media_(max-width:_700px)]:pt-4 [@media_(max-width:_700px)]:pr-4 [@media_(max-width:_700px)]:pb-4 [@media_(max-width:_700px)]:pl-4">
          <span className="text-(--settings-muted) text-[12px]">
            Total storage
          </span>
          <b className="text-(--settings-accent-strong) text-[44px] font-medium tracking-[-.035em] [@media_(max-width:_700px)]:text-[22px] font-(family-name:--hoja-font-ui)">
            {formatBytes(summary.totalBytes)}
          </b>
          <small className="leading-relaxed text-(--settings-muted) text-[12px]">
            Messages and conversation data
          </small>
        </div>
      </div>
      <div className="mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-5.25 [@media_(max-width:_390px)]:items-start">
          <div>
            <h2 className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)">
              Conversation activity
            </h2>
          </div>
          <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
            Most recent first
          </span>
        </div>
        {summary.conversations.length ? (
          <div className="overflow-x-auto border border-line border-solid border-(--settings-line) rounded-[10px]">
            <table className="border-collapse w-full text-[12px] [@media_(max-width:_700px)]:min-w-112.5">
              <caption className="sr-only absolute w-px h-px overflow-hidden [clip:rect(0_0_0_0)]">
                Saved agent conversations, message counts, and storage size
              </caption>
              <thead>
                <tr>
                  <th
                    className="border-b border-line text-left pt-2.75 pr-3.5 pb-2.75 pl-3.5 border-b-(--settings-line) bg-(--settings-subtle) text-(--settings-muted) text-[10px] font-semibold"
                    scope="col"
                  >
                    Last updated
                  </th>
                  <th
                    className="border-b border-line text-left pt-2.75 pr-3.5 pb-2.75 pl-3.5 border-b-(--settings-line) bg-(--settings-subtle) text-(--settings-muted) text-[10px] font-semibold"
                    scope="col"
                  >
                    Messages
                  </th>
                  <th
                    className="border-b border-line text-left pt-2.75 pr-3.5 pb-2.75 pl-3.5 border-b-(--settings-line) bg-(--settings-subtle) text-(--settings-muted) text-[10px] font-semibold"
                    scope="col"
                  >
                    Size
                  </th>
                </tr>
              </thead>
              <tbody>
                {summary.conversations.map((c) => (
                  <tr key={c.id}>
                    <td className="border-b border-line pt-3.25 pr-3.5 pb-3.25 pl-3.5 border-b-(--settings-line) text-(--settings-ink)">
                      {new Date(c.updatedAt).toLocaleString()}
                    </td>
                    <td className="border-b border-line pt-3.25 pr-3.5 pb-3.25 pl-3.5 border-b-(--settings-line) text-(--settings-ink)">
                      {c.messageCount}
                    </td>
                    <td className="border-b border-line pt-3.25 pr-3.5 pb-3.25 pl-3.5 border-b-(--settings-line) text-(--settings-ink)">
                      {formatBytes(c.bytes)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="grid justify-items-center text-center text-sm gap-1.75 pt-7.5! pr-4.5! pb-7.5! pl-4.5! text-(--settings-muted)">
            <span
              className="flex items-center justify-center grid place-items-center w-9 h-9 mb-0.75 rounded-[11px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[12px] font-bold"
              aria-hidden="true"
            >
              0
            </span>
            <b className="font-semibold text-(--settings-ink) text-[12px]">
              No saved conversations
            </b>
            <p className="text-muted max-w-90 mt-0 mr-0 mb-0 ml-0 text-[11px] leading-[1.55]">
              Agent conversations will appear here after you start a
              conversation.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
