import { useId, useState } from "react";
import { MessageCircle, Pencil, Trash2 } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { DropdownMenuItem } from "../../components/ui/menu";
import {
  ConfirmAction,
  DangerZone,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  ResourceRow,
  SettingRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  gql,
  rest,
  useResource,
} from "../ui";

/* Agent conversations: what the agent panel has stored for you, with the same rename and
 * delete the panel offers (PATCH/DELETE /api/agent/threads/*). Message content is never shown
 * here; this page is about what is kept and how much of it. */
type Conversation = {
  id: string;
  title: string;
  messageCount: number;
  bytes: number;
  updatedAt: string;
};

const TITLE_MAX = 60; // agent_threads.TITLE_LIMIT: longer titles are cut by the server.

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(iso: string) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "Unknown"
    : d.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
}

async function load(): Promise<Conversation[]> {
  const [summary, threads] = await Promise.all([
    gql<{
      agentConversations: {
        conversations: {
          id: string;
          messageCount: number;
          bytes: number;
          updatedAt: string;
        }[];
      };
    }>(
      "query AgentConversations{agentConversations{conversations{id messageCount bytes updatedAt}}}",
    ),
    rest<{ threads?: { id: string; title?: string }[] }>(
      "/api/agent/threads/get",
    ),
  ]);
  const titles = new Map(
    (threads?.threads || []).map((t) => [t.id, t.title || ""]),
  );
  return summary.agentConversations.conversations.map((c) => ({
    ...c,
    title: titles.get(c.id) || "New chat",
  }));
}

// Cookie-authenticated writes must be JSON (app._cookie_csrf_error), so DELETE sends `{}` too.
const renameConversation = (id: string, title: string) =>
  rest<{ id: string; title: string }>(
    `/api/agent/threads/update/${encodeURIComponent(id)}`,
    { method: "PATCH", json: { id, title } },
  );
async function deleteConversation(id: string) {
  try {
    await rest(`/api/agent/threads/delete/${encodeURIComponent(id)}`, {
      method: "DELETE",
      json: {},
    });
  } catch (e) {
    // Already gone (deleted from the agent panel in another tab): that is the outcome wanted.
    if (!/not found/i.test(e instanceof Error ? e.message : "")) throw e;
  }
}

function RenameDialog({
  conversation,
  onOpenChange,
  onRenamed,
}: {
  conversation: Conversation | null;
  onOpenChange: (open: boolean) => void;
  onRenamed: (id: string, title: string) => void;
}) {
  const inputId = useId();
  const [title, setTitle] = useState(conversation?.title || "");
  const [lastId, setLastId] = useState<string | null>(null);
  if (conversation && conversation.id !== lastId) {
    setLastId(conversation.id);
    setTitle(conversation.title);
  }
  const trimmed = title.trim();
  const problem = !trimmed
    ? "Enter a name."
    : trimmed.length > TITLE_MAX
      ? `Use ${TITLE_MAX} characters or fewer.`
      : null;
  return (
    <FormDialog
      open={!!conversation}
      onOpenChange={onOpenChange}
      title="Rename conversation"
      description="The new name also shows in the agent panel's history."
      submitLabel="Rename"
      canSubmit={!problem}
      onSubmit={async () => {
        if (!conversation) return;
        const saved = await renameConversation(conversation.id, trimmed);
        onRenamed(conversation.id, saved?.title || trimmed);
      }}
    >
      <Field
        label="Name"
        htmlFor={inputId}
        error={trimmed.length > TITLE_MAX ? problem : null}
        help={`Up to ${TITLE_MAX} characters.`}
      >
        <Input
          id={inputId}
          value={title}
          autoComplete="off"

          autoFocus
          onChange={(e) => setTitle(e.target.value)}
        />
      </Field>
    </FormDialog>
  );
}

export default function AgentConversationsSettings() {
  const conversations = useResource(load);
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [clearing, setClearing] = useState(false);
  const list = conversations.data || [];
  const totalBytes = list.reduce((n, c) => n + c.bytes, 0);
  const totalMessages = list.reduce((n, c) => n + c.messageCount, 0);

  const remove = (id: string) =>
    conversations.setData((all) => all?.filter((c) => c.id !== id));

  return (
    <SettingsPage
      title="Agent conversations"
      description="Chats with the agent are saved to your account so you can pick them up later. Only you can see them."
    >
      <Loadable resource={conversations} rows={4}>
        {(data) => (
          <>
            {data.length > 0 && (
              <SettingsSection title="Storage">
                <SettingRow
                  label="Saved conversations"
                  description={`${totalMessages} ${totalMessages === 1 ? "message" : "messages"} in total`}
                >
                  <span
                    className="text-base font-medium tabular-nums"
                    data-conversation-count=""
                  >
                    {data.length}
                  </span>
                </SettingRow>
                <SettingRow
                  label="Space used"
                  description="Message text and tool results, before compression."
                >
                  <span className="text-base font-medium tabular-nums">
                    {formatBytes(totalBytes)}
                  </span>
                </SettingRow>
              </SettingsSection>
            )}
            <SettingsSection
              title="Conversations"
              description="Most recently active first. Renaming or deleting here also changes the agent panel's history."
              bare
            >
              <SettingsCard>
                {data.length === 0 ? (
                  <EmptyState
                    icon={<MessageCircle />}
                    title="No saved conversations"
                    description="Start a chat from the agent panel. It is saved here so you can come back to it."
                  />
                ) : (
                  <ul aria-label="Saved conversations" className="m-0 p-0">
                    {data.map((c) => (
                      <li
                        key={c.id}
                        className="list-none border-line not-first:border-t"
                      >
                        <ResourceRow
                          title={c.title}
                          meta={`${c.messageCount} ${c.messageCount === 1 ? "message" : "messages"} · ${formatBytes(c.bytes)} · Last active ${formatWhen(c.updatedAt)}`}
                          menuLabel={`Actions for ${c.title}`}
                          actions={
                            <>
                              <DropdownMenuItem onSelect={() => setRenaming(c)}>
                                <Pencil aria-hidden="true" />
                                Rename…
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                destructive
                                onSelect={() => setDeleting(c)}
                              >
                                <Trash2 aria-hidden="true" />
                                Delete…
                              </DropdownMenuItem>
                            </>
                          }
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </SettingsCard>
            </SettingsSection>
            {data.length > 0 && (
              <DangerZone>
                <SettingRow
                  label="Delete all conversations"
                  description={`Removes ${data.length === 1 ? "your saved conversation and its" : `all ${data.length} saved conversations and their`} messages. This can't be undone.`}
                >
                  <Button
                    variant="destructive"
                    onClick={() => setClearing(true)}
                  >
                    Delete all…
                  </Button>
                </SettingRow>
              </DangerZone>
            )}
          </>
        )}
      </Loadable>

      <RenameDialog
        conversation={renaming}
        onOpenChange={(o) => !o && setRenaming(null)}
        onRenamed={(id, title) =>
          conversations.setData((all) =>
            all?.map((c) => (c.id === id ? { ...c, title } : c)),
          )
        }
      />
      <ConfirmAction
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.title || "conversation"}”?`}
        description="The conversation and its messages are removed from your account and from the agent panel. This can't be undone."
        confirmLabel="Delete conversation"
        onConfirm={async () => {
          if (!deleting) return;
          await deleteConversation(deleting.id);
          remove(deleting.id);
        }}
      />
      <ConfirmAction
        open={clearing}
        onOpenChange={setClearing}
        title="Delete all conversations?"
        description={`${list.length === 1 ? "Your saved conversation and its messages are" : `All ${list.length} saved conversations and their messages are`} removed from your account and from the agent panel. This can't be undone.`}
        confirmLabel="Delete all"
        onConfirm={async () => {
          // One at a time, so a failure part-way leaves an accurate list behind.
          for (const c of list) {
            await deleteConversation(c.id);
            remove(c.id);
          }
        }}
      />
    </SettingsPage>
  );
}
