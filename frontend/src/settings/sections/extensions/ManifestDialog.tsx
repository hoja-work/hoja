import * as React from "react";
import { Braces } from "lucide-react";
import { Button } from "../../../components/ui/button";
import { Input, Textarea } from "../../../components/ui/input";
import { Segmented } from "../../../components/ui/switch";
import { Field, FormDialog } from "../../ui";
import type { Extension } from "./api";
import {
  checkBasics,
  checkManifest,
  EMPTY_BASICS,
  EXAMPLE_MANIFEST,
  joinManifest,
  parseJson,
  splitManifest,
  type ManifestBasics,
} from "./manifest";

type Mode = "form" | "json";
type Errors = Partial<Record<keyof ManifestBasics, string>>;

function count(n: number, one: string) {
  return `${n} ${n === 1 ? one : `${one}s`}`;
}

/* Install or edit an extension. The common fields have a form; hooks, events and actions
 * stay in the JSON (they are lists of objects that only an extension author writes). Both
 * views edit the same manifest, and JSON is checked locally before it is sent. */
export function ManifestDialog({
  open,
  onOpenChange,
  editing,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editing: Extension | null;
  onSubmit: (manifest: Record<string, unknown>) => Promise<unknown>;
}) {
  const [mode, setMode] = React.useState<Mode>("form");
  const [basics, setBasics] = React.useState<ManifestBasics>(EMPTY_BASICS);
  const [rest, setRest] = React.useState<Record<string, unknown>>({});
  const [json, setJson] = React.useState("");
  const [jsonError, setJsonError] = React.useState<string | null>(null);
  const [errors, setErrors] = React.useState<Errors>({});
  const ids = React.useId();
  const id = (k: string) => `${ids}-${k}`;

  React.useEffect(() => {
    if (!open) return;
    const source =
      editing?.manifest && typeof editing.manifest === "object"
        ? (editing.manifest as Record<string, unknown>)
        : null;
    const split = source
      ? splitManifest(source)
      : { basics: EMPTY_BASICS, rest: {} };
    setBasics(split.basics);
    setRest(split.rest);
    setJson("");
    setMode("form");
    setErrors({});
    setJsonError(null);
  }, [open, editing]);

  const toJson = () => JSON.stringify(joinManifest(basics, rest), null, 2);

  const switchMode = (next: Mode) => {
    if (next === mode) return;
    if (next === "json") {
      setJson(toJson());
      setJsonError(null);
      setMode("json");
      return;
    }
    const parsed = parseJson(json);
    if (parsed.error) return setJsonError(parsed.error);
    if (
      !parsed.value ||
      typeof parsed.value !== "object" ||
      Array.isArray(parsed.value)
    )
      return setJsonError(
        "The manifest must be a JSON object, starting with { and ending with }.",
      );
    const split = splitManifest(parsed.value as Record<string, unknown>);
    if (editing) split.basics.key = editing.key;
    setBasics(split.basics);
    setRest(split.rest);
    setErrors({});
    setMode("form");
  };

  const set =
    (k: keyof ManifestBasics) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setBasics((b) => ({ ...b, [k]: e.target.value }));
      if (errors[k]) setErrors((x) => ({ ...x, [k]: undefined }));
    };

  const submit = async () => {
    let manifest: Record<string, unknown>;
    if (mode === "form") {
      const found = checkBasics(basics);
      setErrors(found);
      if (Object.keys(found).length)
        throw new Error("Fix the highlighted fields.");
      manifest = joinManifest(basics, rest);
    } else {
      const parsed = parseJson(json);
      const problem = parsed.error ?? checkManifest(parsed.value);
      setJsonError(problem);
      if (problem)
        throw new Error("Fix the manifest first. The problem is shown above.");
      manifest = parsed.value as Record<string, unknown>;
      if (editing && manifest.key !== editing.key) {
        const msg = `The key can't change (it is "${editing.key}"). Install a new extension instead.`;
        setJsonError(msg);
        throw new Error(msg);
      }
    }
    await onSubmit(manifest);
  };

  const hooks = Array.isArray(rest.hooks) ? rest.hooks.length : 0;
  const actions = Array.isArray(rest.actions) ? rest.actions.length : 0;
  const events = Array.isArray(rest.events) ? rest.events.length : 0;

  return (
    <FormDialog
      wide
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Edit ${editing.name}` : "Install an extension"}
      description={
        editing
          ? "Hoja applies only what changed, in one step. The key and credentials stay the same."
          : "Hoja checks every field, then creates the extension's bot, hooks and actions."
      }
      submitLabel={editing ? "Save manifest" : "Install"}
      onSubmit={submit}
    >
      <Segmented
        label="Manifest editor"
        value={mode}
        onValueChange={(v) => switchMode(v as Mode)}
        options={[
          { value: "form", label: "Form" },
          { value: "json", label: "JSON" },
        ]}
      />
      {mode === "form" ? (
        <>
          <div className="grid items-start gap-4 sm:grid-cols-2">
            <Field label="Name" htmlFor={id("name")} error={errors.name}>
              <Input
                id={id("name")}
                value={basics.name}
                onChange={set("name")}
                maxLength={80}
                autoComplete="off"
                aria-invalid={!!errors.name || undefined}
                placeholder="Worktree provisioner"
              />
            </Field>
            <Field
              label="Key"
              htmlFor={id("key")}
              error={errors.key}
              help={
                editing
                  ? "Can't be changed after install."
                  : "Lowercase, numbers and dashes. Can't be changed later."
              }
            >
              <Input
                id={id("key")}
                value={basics.key}
                onChange={set("key")}
                disabled={!!editing}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={!!errors.key || undefined}
                placeholder="worktrees"
              />
            </Field>
          </div>
          <div className="grid items-start gap-4 sm:grid-cols-3">
            <div className="sm:col-span-2">
              <Field
                label="URL"
                htmlFor={id("url")}
                error={errors.url}
                help="Hoja calls this over signed HTTPS. Hook and action paths are added to it."
              >
                <Input
                  id={id("url")}
                  type="url"
                  inputMode="url"
                  value={basics.url}
                  onChange={set("url")}
                  autoComplete="off"
                  spellCheck={false}
                  aria-invalid={!!errors.url || undefined}
                />
              </Field>
            </div>
            <Field
              label="Version"
              htmlFor={id("version")}
              error={errors.version}
            >
              <Input
                id={id("version")}
                value={basics.version}
                onChange={set("version")}
                maxLength={40}
                autoComplete="off"
                aria-invalid={!!errors.version || undefined}
              />
            </Field>
          </div>
          <div className="grid gap-1.5">
            <span className="text-sm font-medium" aria-hidden="true">
              Access
            </span>
            <Segmented
              label="Access"
              value={basics.access}
              onValueChange={(v) =>
                setBasics((b) => ({
                  ...b,
                  access: v === "read_write" ? "read_write" : "read",
                }))
              }
              options={[
                { value: "read", label: "Read only" },
                { value: "read_write", label: "Read and write" },
              ]}
            />
            <p className="m-0 text-sm text-muted-foreground">
              {basics.access === "read_write"
                ? "The extension's bot can create and change tasks. Its changes are made as the bot, never as you."
                : "The extension can read tasks but can't change anything."}
            </p>
          </div>
          <Field label="Description" htmlFor={id("description")} optional>
            <Textarea
              id={id("description")}
              value={basics.description}
              onChange={set("description")}
              rows={2}
            />
          </Field>
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-subtle px-3 py-2 text-sm text-muted-foreground">
            <span>
              {hooks + actions + events === 0
                ? "No hooks, events or actions yet."
                : `${count(hooks, "hook")}, ${count(events, "event subscription")}, ${count(actions, "action")}.`}{" "}
              These are edited in JSON.
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => switchMode("json")}
            >
              <Braces className="size-3.5" aria-hidden="true" />
              Edit in JSON
            </Button>
          </div>
        </>
      ) : (
        <Field
          label="Manifest JSON"
          htmlFor={id("json")}
          error={jsonError}
          help="Fields: key, name, version, url, access, description, events, hooks, actions."
        >
          <Textarea
            id={id("json")}
            value={json}
            onChange={(e) => {
              setJson(e.target.value);
              if (jsonError) setJsonError(null);
            }}
            onBlur={() => {
              if (!json.trim()) return;
              const parsed = parseJson(json);
              setJsonError(parsed.error ?? checkManifest(parsed.value));
            }}
            rows={16}
            spellCheck={false}
            autoComplete="off"
            aria-invalid={!!jsonError || undefined}
            className="font-mono text-sm"
          />
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => {
                const parsed = parseJson(json);
                if (parsed.error) return setJsonError(parsed.error);
                setJson(JSON.stringify(parsed.value, null, 2));
                setJsonError(null);
              }}
            >
              Format
            </Button>
            {!editing && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setJson(JSON.stringify(EXAMPLE_MANIFEST, null, 2));
                  setJsonError(null);
                }}
              >
                Insert example
              </Button>
            )}
          </div>
        </Field>
      )}
    </FormDialog>
  );
}
