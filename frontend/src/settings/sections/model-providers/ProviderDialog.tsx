import * as React from "react";
import { Input } from "../../../components/ui/input";
import { Field, FormDialog } from "../../ui";
import { createProvider, updateProvider, type ModelProvider } from "./api";

type Draft = {
  name: string;
  provider: string;
  modelId: string;
  baseUrl: string;
  apiKey: string;
};

type Errors = Partial<Record<keyof Draft, string>>;

const EMPTY: Draft = {
  name: "",
  provider: "openai",
  modelId: "",
  baseUrl: "",
  apiKey: "",
};

function validate(d: Draft, editing: ModelProvider | null): Errors {
  const e: Errors = {};
  if (!d.name.trim()) e.name = "Enter a name people will recognise.";
  else if (d.name.trim().length > 80)
    e.name = "Keep the name to 80 characters.";
  if (!editing && !d.provider.trim()) e.provider = "Enter the provider.";
  if (!d.modelId.trim()) e.modelId = "Enter the model ID, for example gpt-4o.";
  const url = d.baseUrl.trim();
  if (url) {
    let parsed: URL | null = null;
    try {
      parsed = new URL(url);
    } catch {
      parsed = null;
    }
    if (!parsed || !/^https?:$/.test(parsed.protocol))
      e.baseUrl = "Enter a full URL, for example https://api.openai.com/v1.";
  } else if (editing?.baseUrl) {
    e.baseUrl =
      "A base URL can't be removed once set. Enter the URL to keep using.";
  }
  if (!editing && !d.apiKey) e.apiKey = "Paste the API key for this model.";
  return e;
}

/* Add or edit a provider. Edit sends only what changed; the API key field is never
 * pre-filled (the server cannot return it) and a blank field keeps the stored key. */
export function ProviderDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: ModelProvider | null;
  onSaved: (p: ModelProvider) => void;
}) {
  const [draft, setDraft] = React.useState<Draft>(EMPTY);
  const [errors, setErrors] = React.useState<Errors>({});
  const ids = React.useId();
  React.useEffect(() => {
    if (!open) return;
    setErrors({});
    setDraft(
      editing
        ? {
            name: editing.name,
            provider: editing.provider,
            modelId: editing.modelId,
            baseUrl: editing.baseUrl ?? "",
            apiKey: "",
          }
        : EMPTY,
    );
  }, [open, editing]);

  const set = (k: keyof Draft) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setDraft((d) => ({ ...d, [k]: e.target.value }));
    if (errors[k]) setErrors((x) => ({ ...x, [k]: undefined }));
  };

  const changed = editing
    ? draft.name.trim() !== editing.name ||
      draft.modelId.trim() !== editing.modelId ||
      draft.baseUrl.trim() !== (editing.baseUrl ?? "") ||
      draft.apiKey !== ""
    : true;

  const submit = async () => {
    const found = validate(draft, editing);
    setErrors(found);
    if (Object.keys(found).length)
      throw new Error("Fix the highlighted fields.");
    if (editing) {
      const patch: Parameters<typeof updateProvider>[1] = {};
      if (draft.name.trim() !== editing.name) patch.name = draft.name.trim();
      if (draft.modelId.trim() !== editing.modelId)
        patch.modelId = draft.modelId.trim();
      if (draft.baseUrl.trim() && draft.baseUrl.trim() !== editing.baseUrl)
        patch.baseUrl = draft.baseUrl.trim();
      if (draft.apiKey) patch.apiKey = draft.apiKey;
      onSaved(await updateProvider(editing.id, patch));
    } else {
      onSaved(
        await createProvider({
          name: draft.name.trim(),
          provider: draft.provider.trim(),
          modelId: draft.modelId.trim(),
          baseUrl: draft.baseUrl.trim() || null,
          apiKey: draft.apiKey,
        }),
      );
    }
  };

  const id = (k: string) => `${ids}-${k}`;
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit ${editing.name}` : "Add a model provider"}
      description={
        editing
          ? "Changes apply to the next agent run."
          : "Connect any OpenAI-compatible model. The key is encrypted and can't be viewed again."
      }
      submitLabel={editing ? "Save changes" : "Add provider"}
      canSubmit={changed}
      onSubmit={submit}
    >
      <Field label="Name" htmlFor={id("name")} error={errors.name}>
        <Input
          id={id("name")}
          value={draft.name}
          onChange={set("name")}
          maxLength={80}
          autoComplete="off"
          aria-invalid={!!errors.name || undefined}
          placeholder="GPT-4o for triage"
        />
      </Field>
      <div className="grid items-start gap-4 sm:grid-cols-2">
        <Field
          label="Provider"
          htmlFor={id("provider")}
          error={errors.provider}
          help={
            editing
              ? "Can't be changed. Add a new provider instead."
              : "Who serves the model, e.g. openai, anthropic or openrouter. Any OpenAI-compatible service works with a base URL."
          }
        >
          <Input
            id={id("provider")}
            value={draft.provider}
            onChange={set("provider")}
            disabled={!!editing}
            autoComplete="off"
            aria-invalid={!!errors.provider || undefined}
          />
        </Field>
        <Field label="Model ID" htmlFor={id("model")} error={errors.modelId}>
          <Input
            id={id("model")}
            value={draft.modelId}
            onChange={set("modelId")}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={!!errors.modelId || undefined}
            placeholder="gpt-4o"
          />
        </Field>
      </div>
      <Field
        label="Base URL"
        htmlFor={id("url")}
        optional={!editing?.baseUrl}
        error={errors.baseUrl}
        help="The OpenAI-compatible endpoint. Must be https and publicly reachable."
      >
        <Input
          id={id("url")}
          type="url"
          inputMode="url"
          value={draft.baseUrl}
          onChange={set("baseUrl")}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={!!errors.baseUrl || undefined}
          placeholder="https://api.openai.com/v1"
        />
      </Field>
      <Field
        label={editing ? "Replace API key" : "API key"}
        htmlFor={id("key")}
        optional={!!editing}
        error={errors.apiKey}
        help={
          editing
            ? editing.hasApiKey
              ? "A key is saved and encrypted. Leave this blank to keep it."
              : "No key is saved. Agents can't use this provider until you add one."
            : "Stored encrypted. Nobody, including admins, can view it again."
        }
      >
        <Input
          id={id("key")}
          type="password"
          value={draft.apiKey}
          onChange={set("apiKey")}
          autoComplete="new-password"
          spellCheck={false}
          aria-invalid={!!errors.apiKey || undefined}
          placeholder={editing?.hasApiKey ? "••••••••  (saved)" : "sk-…"}
        />
      </Field>
    </FormDialog>
  );
}
