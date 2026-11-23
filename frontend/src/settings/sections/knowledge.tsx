import * as React from "react";
import { BookOpen, Eye, Plus } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Segmented } from "../../components/ui/switch";
import { toast } from "../../components/ui/toast";
import {
  ConfirmAction,
  EmptyState,
  Loadable,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  useResource,
} from "../ui";
import {
  createDoc,
  deleteDoc,
  loadKnowledge,
  updateDoc,
  type KnowledgeData,
  type KnowledgeDoc,
  type Scope,
} from "./knowledge/api";
import {
  DocDialog,
  DocRow,
  PreviewDialog,
  scopeLabel,
} from "./knowledge/parts";

/* Settings → Knowledge (HOJA-734). Markdown documents injected into every agent's context.
 * One behaviour per value: `enabled` is an instant switch on the row; title, scope and body
 * are edited in a dialog that closes only after the server saved them. */

const ORDER: Record<Scope, number> = { workspace: 0, team: 1, project: 2 };

export default function KnowledgeSettings() {
  const resource = useResource<KnowledgeData>(loadKnowledge);
  const [filter, setFilter] = React.useState<"all" | Scope>("all");
  const [editing, setEditing] = React.useState<KnowledgeDoc | "new" | null>(
    null,
  );
  const [deleting, setDeleting] = React.useState<KnowledgeDoc | null>(null);
  const [preview, setPreview] = React.useState(false);

  const data = resource.data;
  const teams = data?.teams ?? [];
  const projects = data?.projects ?? [];

  const replace = (doc: KnowledgeDoc) =>
    resource.setData((d) =>
      d ? { ...d, docs: d.docs.map((x) => (x.id === doc.id ? doc : x)) } : d,
    );

  return (
    <SettingsPage
      title="Knowledge"
      description={
        <>
          Standards, architecture notes and conventions that agents read before
          they work. Every enabled document is added to Agent chat, delegated
          agent runs and MCP clients, so keep them short and current.
        </>
      }
      actions={
        <>
          <Button onClick={() => setPreview(true)} disabled={!data}>
            <Eye className="size-3.5" aria-hidden="true" />
            Preview
          </Button>
          <Button
            variant="primary"
            onClick={() => setEditing("new")}
            disabled={!data}
          >
            <Plus className="size-3.5" aria-hidden="true" />
            New document
          </Button>
        </>
      }
    >
      <SettingsSection
        title="Documents"
        description="Turn a document off to stop giving it to agents without deleting it."
        action={
          data && data.docs.length > 0 ? (
            <Segmented
              label="Show documents for"
              value={filter}
              onValueChange={(v) => setFilter(v as typeof filter)}
              options={[
                { value: "all", label: "All" },
                { value: "workspace", label: "Workspace" },
                { value: "team", label: "Teams" },
                { value: "project", label: "Projects" },
              ]}
            />
          ) : undefined
        }
        bare
      >
        <Loadable
          resource={resource}
          isEmpty={(d) => d.docs.length === 0}
          empty={
            <EmptyState
              icon={<BookOpen />}
              title="No knowledge yet"
              description="Add your first document, such as coding conventions or how your product is put together, and every agent will follow it."
              action={
                <Button variant="primary" onClick={() => setEditing("new")}>
                  <Plus className="size-3.5" aria-hidden="true" />
                  New document
                </Button>
              }
            />
          }
        >
          {(d) => {
            const shown = d.docs
              .filter((x) => filter === "all" || x.scope === filter)
              .sort(
                (a, b) =>
                  ORDER[a.scope] - ORDER[b.scope] ||
                  a.title.localeCompare(b.title),
              );
            return (
              <SettingsCard>
                {shown.length ? (
                  <div role="list" aria-label="Knowledge documents">
                    {shown.map((doc) => (
                      <div
                        role="listitem"
                        key={doc.id}
                        className="border-line not-first:border-t"
                      >
                        <DocRow
                          doc={doc}
                          scope={scopeLabel(doc, teams, projects)}
                          onSaved={replace}
                          onEdit={() => setEditing(doc)}
                          onDelete={() => setDeleting(doc)}
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No documents here"
                    description="Nothing is scoped this way yet."
                    action={
                      <Button size="sm" onClick={() => setFilter("all")}>
                        Show all
                      </Button>
                    }
                  />
                )}
              </SettingsCard>
            );
          }}
        </Loadable>
      </SettingsSection>

      <DocDialog
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        doc={editing && editing !== "new" ? editing : null}
        teams={teams}
        projects={projects}
        onSubmit={async (input) => {
          if (editing && editing !== "new") {
            replace(await updateDoc(editing.id, input));
          } else {
            const doc = await createDoc(input);
            resource.setData((d) => (d ? { ...d, docs: [...d.docs, doc] } : d));
            if (filter !== "all" && filter !== doc.scope) setFilter("all");
          }
        }}
      />

      <PreviewDialog
        open={preview}
        onOpenChange={setPreview}
        teams={teams}
        projects={projects}
      />

      <ConfirmAction
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={deleting ? `Delete "${deleting.title}"?` : "Delete document?"}
        description="Agents stop receiving it immediately. This can't be undone; to pause it instead, turn it off."
        confirmLabel="Delete document"
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await deleteDoc(deleting.id);
          } catch (e) {
            void resource.reload();
            throw e;
          }
          const gone = deleting;
          resource.setData((d) =>
            d ? { ...d, docs: d.docs.filter((x) => x.id !== gone.id) } : d,
          );
          toast({ title: "Document deleted", description: gone.title });
        }}
      />
    </SettingsPage>
  );
}
