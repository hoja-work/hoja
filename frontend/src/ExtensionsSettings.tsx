import React, { useEffect, useState } from "react";
import {
  ExtensionError,
  clearExtensionActionsCache,
  errorText,
  fetchExtensions,
  installExtension,
  uninstallExtension,
  updateExtension,
  type Extension,
} from "./extensions";

/* Administration -> Extensions. Monochrome, shape-first: the settings design system has
 * no accent colours, so hierarchy comes from borders, spacing and type weight only. */

const EXAMPLE_MANIFEST = JSON.stringify(
  {
    key: "worktrees",
    name: "Worktree provisioner",
    version: "1.0.0",
    url: "https://ext.example.com",
    access: "read_write",
    events: [{ key: "issues", resourceTypes: ["Issue"], teamId: "ENG" }],
    hooks: [
      {
        key: "guard",
        on: "issue.beforeCreate",
        path: "/hooks/guard",
        failurePolicy: "allow",
      },
    ],
    actions: [
      {
        key: "provision",
        title: "Provision environment",
        entity: "Issue",
        path: "/actions/provision",
      },
    ],
  },
  null,
  2,
);

type Notice = { kind: "status" | "error"; text: string };
type Editor = { id: string; text: string; error: string };

function formatDate(value?: string | null) {
  if (!value) return "Unknown";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleString();
}

/** Parse before sending: the textarea should explain a broken manifest without a round trip. */
function parseManifest(text: string): {
  value?: Record<string, unknown>;
  error?: string;
} {
  const trimmed = text.trim();
  if (!trimmed) return { error: "Paste a manifest to continue." };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (e) {
    return { error: `Invalid JSON: ${errorText(e)}` };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {
      error:
        "The manifest must be a JSON object with key, name, version, url and access.",
    };
  }
  return { value: parsed as Record<string, unknown> };
}

export function ExtensionsSettings() {
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [manifestText, setManifestText] = useState("");
  const [formError, setFormError] = useState("");
  const [installing, setInstalling] = useState(false);
  const [secrets, setSecrets] = useState<{
    name: string;
    apiKey: string;
    signingSecret: string;
  } | null>(null);
  const [copied, setCopied] = useState<"apiKey" | "signingSecret" | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let live = true;
    // Revalidate in place: the list stays on screen; only the first load shows a loading line.
    fetchExtensions()
      .then((list) => {
        if (live) {
          setExtensions(list);
          setLoadError("");
        }
      })
      .catch((e) => {
        if (live) setLoadError(errorText(e));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [refresh]);

  const install = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setNotice(null);
    setCopied(null);
    const parsed = parseManifest(manifestText);
    if (parsed.error) {
      setFormError(parsed.error);
      return;
    }
    setInstalling(true);
    setSecrets(null);
    try {
      const payload = await installExtension(parsed.value);
      if (!payload?.success || !payload.extension)
        throw new ExtensionError("The extension was not installed.");
      const installed = payload.extension;
      setManifestText("");
      setSecrets({
        name: installed.name || installed.key,
        apiKey: payload.apiKey || "",
        signingSecret: payload.signingSecret || "",
      });
      clearExtensionActionsCache();
      setNotice({
        kind: "status",
        text: `Installed ${installed.name || installed.key}. Copy its credentials now — they are not shown again.`,
      });
      setRefresh((x) => x + 1);
    } catch (err) {
      setFormError(errorText(err));
    } finally {
      setInstalling(false);
    }
  };

  const copyValue = async (kind: "apiKey" | "signingSecret", value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
    } catch {
      setNotice({
        kind: "error",
        text: "Copy failed. Select the value and copy it manually.",
      });
    }
  };

  const startEdit = (ext: Extension) => {
    setCopied(null);
    setNotice(null);
    setConfirmId(null);
    setEditor({
      id: ext.id,
      text: JSON.stringify(ext.manifest ?? {}, null, 2),
      error: "",
    });
  };

  const saveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editor) return;
    const parsed = parseManifest(editor.text);
    if (parsed.error) {
      setEditor({ ...editor, error: parsed.error });
      return;
    }
    setSavingId(editor.id);
    setNotice(null);
    try {
      const payload = await updateExtension(editor.id, parsed.value);
      if (!payload?.success)
        throw new ExtensionError("The manifest was rejected.");
      clearExtensionActionsCache();
      setEditor(null);
      setNotice({
        kind: "status",
        text: "Manifest updated. Hoja reconciled the extension in one transaction.",
      });
      setRefresh((x) => x + 1);
    } catch (err) {
      setEditor((current) =>
        current ? { ...current, error: errorText(err) } : current,
      );
    } finally {
      setSavingId(null);
    }
  };

  const uninstall = async (ext: Extension) => {
    setBusyId(ext.id);
    setNotice(null);
    try {
      const payload = await uninstallExtension(ext.id);
      if (!payload?.success)
        throw new ExtensionError("The extension was not uninstalled.");
      if (editor?.id === ext.id) setEditor(null);
      setConfirmId(null);
      clearExtensionActionsCache();
      setNotice({
        kind: "status",
        text: `Uninstalled ${ext.name || ext.key}. Its bot and credentials are revoked.`,
      });
      setRefresh((x) => x + 1);
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="w-[min(100%,var(--ds-settings-column-width))] mt-0 mr-auto mb-0 ml-auto pt-(--ds-settings-column-padding) pr-(--ds-settings-column-padding) pb-16 pl-(--ds-settings-column-padding) font-(family-name:--hoja-font-ui) [font-optical-sizing:auto] min-w-0 shell-phone:pt-0 text-(--settings-ink) max-w-(--ds-settings-column-width) [@media_(max-width:_700px)]:pt-0 [html[data-theme=dark]_&]:bg-transparent [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) [--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-on-accent:#fff] [--settings-danger:#242424] [--settings-success:#242424] [html[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [html[data-theme=dark]_&]:[--settings-on-accent:#171717] [html[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [html[data-theme=dark]_&]:[--settings-muted:#aaa] [html[data-theme=dark]_&]:[--settings-line:#353535] [html[data-theme=dark]_&]:[--settings-card:#1b1b1b] [html[data-theme=dark]_&]:[--settings-subtle:#262626] [html[data-theme=dark]_&]:[--settings-danger:#eee] [html[data-theme=dark]_&]:[--settings-success:#d8d8d8]">
      <header className="mt-0 mr-0 mb-8 ml-0 [@media_(max-width:_700px)]:mb-6.75 pb-0 border-b-0 [@media_(max-width:_700px)]:pb-5">
        <h1 className="text-(length:--ds-settings-title-size) tracking-[-.035em] mt-0 mr-0 mb-2 ml-0 font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) text-(--settings-ink) font-(family-name:--hoja-font-ui) [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Extensions
        </h1>
        <p className="text-(--settings-muted) text-[13px] leading-[1.6] wrap-anywhere max-w-full mt-0 mr-0 mb-0 ml-0 [@media_(max-width:_700px)]:text-[13px]">
          Extensions run outside Hoja and are called over signed HTTPS. Each
          install gets its own bot identity, so extension writes are never made
          as you.
        </p>
      </header>

      <div className="grid min-w-0 gap-0">
        {secrets && (
          <div
            className="grid border border-line gap-2.5 min-w-0 mt-0 mr-0 mb-4.5 ml-0 pt-4.5 pr-4.5 pb-4.5 pl-4.5 border-solid border-[color-mix(in_srgb,var(--settings-accent)_28%,var(--settings-line))] rounded-[13px] bg-(--settings-subtle)"
            role="status"
          >
            <b className="font-semibold text-(--settings-ink) text-[13px]">
              Copy the credentials for “{secrets.name}” now
            </b>
            <p className="mt-0 mr-0 mb-0 ml-0 text-(--settings-muted) text-[12px] leading-[1.55]">
              These are shown once. Store them in the extension’s secret store;
              Hoja keeps only the hash of the API key.
            </p>
            {secrets.apiKey ? (
              <div className="flex flex-col flex-wrap border border-line sm:flex-row sm:items-center items-center gap-2.5 min-w-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border-solid border-(--settings-line) rounded-[9px] bg-(--settings-card) [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:items-stretch">
                <span className="shrink-0 flex-none text-(--settings-muted) text-[10px] font-bold tracking-[.08em] uppercase">
                  API key
                </span>
                <code
                  className="break-all flex-[1_1_220px] min-w-0 wrap-anywhere text-(--settings-ink) font-[ui-monospace,SFMono-Regular,Menlo,monospace] text-[12px]"
                  aria-label="New extension API key"
                >
                  {secrets.apiKey}
                </code>
                <button
                  type="button"
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] [@media_(max-width:_700px)]:w-full [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => void copyValue("apiKey", secrets.apiKey)}
                >
                  {copied === "apiKey" ? "Copied" : "Copy key"}
                </button>
              </div>
            ) : (
              <p className="block text-(--settings-danger) mt-0 mr-0 mb-0 ml-0 text-[12px] leading-[1.55]">
                The server returned no API key. Uninstall and install again to
                receive one.
              </p>
            )}
            {secrets.signingSecret ? (
              <div className="flex flex-col flex-wrap border border-line sm:flex-row sm:items-center items-center gap-2.5 min-w-0 pt-2.25 pr-2.75 pb-2.25 pl-2.75 border-solid border-(--settings-line) rounded-[9px] bg-(--settings-card) [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:items-stretch">
                <span className="shrink-0 flex-none text-(--settings-muted) text-[10px] font-bold tracking-[.08em] uppercase">
                  Signing secret
                </span>
                <code
                  className="break-all flex-[1_1_220px] min-w-0 wrap-anywhere text-(--settings-ink) font-[ui-monospace,SFMono-Regular,Menlo,monospace] text-[12px]"
                  aria-label="New extension signing secret"
                >
                  {secrets.signingSecret}
                </code>
                <button
                  type="button"
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] [@media_(max-width:_700px)]:w-full [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() =>
                    void copyValue("signingSecret", secrets.signingSecret)
                  }
                >
                  {copied === "signingSecret" ? "Copied" : "Copy secret"}
                </button>
              </div>
            ) : (
              <p className="block text-(--settings-danger) mt-0 mr-0 mb-0 ml-0 text-[12px] leading-[1.55]">
                The server returned no signing secret. Uninstall and install
                again to receive one.
              </p>
            )}
            <button
              type="button"
              className="justify-self-start p-0 underline shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 block mt-0.5 mr-0 mb-0 ml-0 bg-transparent text-(--settings-accent-strong) text-[12px] font-[inherit] focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2"
              onClick={() => {
                setSecrets(null);
                setCopied(null);
              }}
            >
              I’ve saved them
            </button>
          </div>
        )}

        <section
          className="mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          aria-labelledby="extension-install-title"
        >
          <h2
            className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
            id="extension-install-title"
          >
            Install from a manifest
          </h2>
          <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5">
            Paste the extension manifest. Hoja validates every field before it
            creates the bot, hooks and actions.
          </p>
          <form
            className="grid gap-3.5 max-w-none w-full min-w-0"
            onSubmit={install}
            noValidate
          >
            <label className="grid gap-1.5 text-[12px] font-[550] text-foreground min-w-0">
              Manifest JSON
              <textarea
                className="resize-y border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 font-[ui-monospace,SFMono-Regular,Menlo,monospace] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-(--settings-line) bg-(--settings-card) text-(--settings-ink) [@media_(max-width:_700px)]:text-[16px]! w-full min-h-42.5 pt-3 pr-3.25 pb-3 pl-3.25 border-solid rounded-[10px] text-[12px] leading-[1.6]"
                aria-label="Extension manifest JSON"
                rows={12}
                spellCheck={false}
                value={manifestText}
                placeholder='{"key":"worktrees","name":"Worktree provisioner", …}'
                onChange={(e) => {
                  setManifestText(e.target.value);
                  setFormError("");
                }}
              />
            </label>
            {formError && (
              <p
                className="block text-muted mt-0 mr-0 mb-0 ml-0 text-[12px]"
                role="alert"
              >
                {formError}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2.25">
              <button
                className="inline-flex items-center justify-center border border-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-lg bg-(--settings-accent-strong) text-(--settings-on-accent) pt-0 pr-4 pb-0 pl-4 font-[620] shadow-[0_1px_2px_#24242412] hover:filter-[brightness(1.08)] min-h-9 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--settings-accent-strong)_88%,var(--settings-ink))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] [html[data-theme=dark]_&]:text-[#171719] justify-self-start font-[inherit] h-9 min-w-30 focus-visible:outline-solid focus-visible:outline-(--settings-accent) border-solid border-(--settings-accent-strong) text-[12px] [@media_(max-width:_700px)]:flex-[1_1_100%] [@media_(max-width:_700px)]:min-h-10 [@media_(max-width:_700px)]:justify-center"
                disabled={installing || !manifestText.trim()}
              >
                {installing ? "Installing…" : "Install extension"}
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-0 pr-4 pb-0 pl-4 font-[inherit] h-9 min-w-30 justify-self-start focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] [@media_(max-width:_700px)]:flex-[1_1_100%] [@media_(max-width:_700px)]:min-h-10 [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                onClick={() => {
                  setManifestText(EXAMPLE_MANIFEST);
                  setFormError("");
                }}
              >
                Insert example manifest
              </button>
              {manifestText !== "" && (
                <button
                  type="button"
                  className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-0 pr-4 pb-0 pl-4 font-[inherit] h-9 min-w-30 justify-self-start focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] [@media_(max-width:_700px)]:flex-[1_1_100%] [@media_(max-width:_700px)]:min-h-10 [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                  onClick={() => {
                    setManifestText("");
                    setFormError("");
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            <small className="block mt-0 mr-0 mb-0 ml-0 text-(--settings-muted) text-[11px] leading-[1.55]">
              Hook and action paths are joined to the extension URL; absolute
              URLs are rejected.
            </small>
          </form>
        </section>

        {notice && (
          <p
            role={notice.kind === "error" ? "alert" : "status"}
            className={
              (notice.kind === "error"
                ? "border border-line"
                : "border border-line") +
              " block text-(--settings-danger) mt-0 mr-0 mb-4.5 ml-0 pt-2.75 pr-3.5 pb-2.75 pl-3.5 border border-solid border-(--settings-line) rounded-[10px] bg-(--settings-card) text-[12px] leading-[1.55] border-[color-mix(in_srgb,var(--settings-danger)_30%,var(--settings-line))]"
            }
          >
            {notice.text}
          </p>
        )}

        <section
          className="mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]"
          aria-labelledby="extension-list-title"
        >
          <div className="flex flex-wrap items-center justify-between gap-4 mb-5.25 [@media_(max-width:_390px)]:items-start">
            <div>
              <h2
                className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)"
                id="extension-list-title"
              >
                Installed extensions
              </h2>
            </div>
            <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
              {extensions.length}{" "}
              {extensions.length === 1 ? "extension" : "extensions"}
            </span>
          </div>

          {loading && (
            <p
              role="status"
              className="grid justify-items-center border border-dashed border-line text-center text-[13px] leading-[1.6] text-muted gap-1.75 pt-6.5 pr-4 pb-6.5 pl-4 border-(--settings-line) rounded-[11px]"
            >
              Loading extensions…
            </p>
          )}
          {!loading && loadError && (
            <div
              className="grid justify-items-center border border-dashed border-line text-center text-sm gap-1.75 pt-6.5 pr-4 pb-6.5 pl-4 border-(--settings-line) rounded-[11px] text-(--settings-muted)"
              role="alert"
            >
              <b className="font-semibold text-(--settings-ink) text-[12px]">
                Extensions could not be loaded
              </b>
              <p className="text-muted max-w-105 mt-0 mr-0 mb-0 ml-0 wrap-anywhere text-[11px] leading-[1.55]">
                {loadError}
              </p>
              <button
                type="button"
                className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] shell-phone:min-w-11! shell-phone:min-h-11!"
                onClick={() => {
                  setLoading(true);
                  setRefresh((x) => x + 1);
                }}
              >
                Retry
              </button>
            </div>
          )}
          {!loading && !loadError && !extensions.length && (
            <div className="grid justify-items-center border border-dashed border-line text-center text-sm gap-1.75 pt-6.5 pr-4 pb-6.5 pl-4 border-(--settings-line) rounded-[11px] text-(--settings-muted)">
              <b className="font-semibold text-(--settings-ink) text-[12px]">
                No extensions installed
              </b>
              <p className="text-muted max-w-105 mt-0 mr-0 mb-0 ml-0 wrap-anywhere text-[11px] leading-[1.55]">
                Install one from a manifest above. Its hooks and actions appear
                here and in the task menu.
              </p>
            </div>
          )}

          {!loading && !loadError && extensions.length > 0 && (
            <ul
              className="grid list-none gap-3 min-w-0 mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0"
              aria-label="Installed extensions"
            >
              {extensions.map((ext) => {
                const isEditing = editor?.id === ext.id;
                const isConfirming = confirmId === ext.id;
                const saving = savingId === ext.id;
                const busy = busyId === ext.id;
                return (
                  <li
                    className="grid border border-line gap-3.5 min-w-0 pt-4 pr-4 pb-4 pl-4 border-solid border-(--settings-line) rounded-xl bg-(--settings-card) [@media_(max-width:_700px)]:pt-3.5 [@media_(max-width:_700px)]:pr-3.5 [@media_(max-width:_700px)]:pb-3.5 [@media_(max-width:_700px)]:pl-3.5"
                    key={ext.id}
                  >
                    <div className="flex flex-col items-start justify-between sm:flex-row sm:flex-wrap gap-[10px_14px] flex-wrap [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:items-stretch">
                      <div className="flex flex-wrap items-center gap-2.25 min-w-0">
                        <b className="text-foreground text-[13px] font-[650]">
                          {ext.name}
                        </b>
                        <code className="border border-line pt-px pr-1.5 pb-px pl-1.5 border-solid border-(--settings-line) rounded-md bg-(--settings-subtle) text-(--settings-muted) text-[11px]">
                          {ext.key}
                        </code>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className={
                            `inline-flex items-center border border-line whitespace-nowrap ${ext.enabled ? "" : ""}` +
                            " inline-flex items-center w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold whitespace-nowrap"
                          }
                        >
                          {ext.enabled ? "Enabled" : "Disabled"}
                        </span>
                        <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
                          {ext.access === "read_write"
                            ? "Read and write"
                            : "Read only"}
                        </span>
                        <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
                          v{ext.version}
                        </span>
                      </div>
                    </div>

                    <dl className="flex min-w-0 flex-col grid grid-cols-[auto_minmax(0,1fr)] gap-[5px_12px] mt-0 mr-0 mb-0 ml-0 text-[11px] [@media_(max-width:_700px)]:grid-cols-[minmax(0,1fr)] [@media_(max-width:_700px)]:gap-0.5">
                      <dt className="text-(--settings-muted) [@media_(max-width:_700px)]:mt-1.5">
                        Endpoint
                      </dt>
                      <dd className="break-all min-w-0 mt-0 mr-0 mb-0 ml-0 wrap-anywhere text-(--settings-ink)">
                        <code className="min-w-0 break-all text-xs font-[ui-monospace,SFMono-Regular,Menlo,monospace]">
                          {ext.url}
                        </code>
                      </dd>
                      <dt className="text-(--settings-muted) [@media_(max-width:_700px)]:mt-1.5">
                        Bot identity
                      </dt>
                      <dd className="break-all min-w-0 mt-0 mr-0 mb-0 ml-0 wrap-anywhere text-(--settings-ink)">
                        {ext.botUser
                          ? ext.botUser.name || ext.botUser.id
                          : "None"}
                      </dd>
                      <dt className="text-(--settings-muted) [@media_(max-width:_700px)]:mt-1.5">
                        Updated
                      </dt>
                      <dd className="break-all min-w-0 mt-0 mr-0 mb-0 ml-0 wrap-anywhere text-(--settings-ink)">
                        {formatDate(ext.updatedAt)}
                      </dd>
                    </dl>

                    <div className="grid border-t border-line gap-2 min-w-0 pt-3 border-t-(--settings-line)">
                      <h3 className="flex flex-wrap items-center gap-2 mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-[11px] font-bold tracking-[.08em] uppercase">
                        Hooks{" "}
                        <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
                          {ext.hooks.length}
                        </span>
                      </h3>
                      {ext.hooks.length ? (
                        <ul className="grid list-none gap-1.5 min-w-0 mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0">
                          {ext.hooks.map((hook) => (
                            <li
                              className="flex flex-wrap items-center border border-line gap-2 min-w-0 pt-1.75 pr-2.5 pb-1.75 pl-2.5 border-solid border-(--settings-line) rounded-lg bg-(--settings-subtle) text-[11px]"
                              key={`${ext.id}-hook-${hook.key}`}
                            >
                              <code className="min-w-0 break-all text-xs wrap-anywhere text-(--settings-ink) font-[ui-monospace,SFMono-Regular,Menlo,monospace]">
                                {hook.on}
                              </code>
                              <span className="text-(--settings-muted)">
                                {hook.key}
                              </span>
                              <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
                                {hook.failurePolicy === "block"
                                  ? "Blocks on failure"
                                  : "Fails open"}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-0 mr-0 mb-0 ml-0 text-(--settings-muted) text-[11px] leading-[1.55]">
                          No hooks. Hoja never waits on this extension before
                          writing.
                        </p>
                      )}
                    </div>

                    <div className="grid border-t border-line gap-2 min-w-0 pt-3 border-t-(--settings-line)">
                      <h3 className="flex flex-wrap items-center gap-2 mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-[11px] font-bold tracking-[.08em] uppercase">
                        Actions{" "}
                        <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
                          {ext.actions.length}
                        </span>
                      </h3>
                      {ext.actions.length ? (
                        <ul className="grid list-none gap-1.5 min-w-0 mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0">
                          {ext.actions.map((action) => (
                            <li
                              className="flex flex-wrap items-center border border-line gap-2 min-w-0 pt-1.75 pr-2.5 pb-1.75 pl-2.5 border-solid border-(--settings-line) rounded-lg bg-(--settings-subtle) text-[11px]"
                              key={action.id}
                            >
                              <b className="text-foreground text-[11px] font-[650]">
                                {action.title}
                              </b>
                              <code className="min-w-0 break-all text-xs wrap-anywhere text-(--settings-ink) font-[ui-monospace,SFMono-Regular,Menlo,monospace]">
                                {action.key}
                              </code>
                              <span className="text-(--settings-muted)">
                                {action.entity}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-0 mr-0 mb-0 ml-0 text-(--settings-muted) text-[11px] leading-[1.55]">
                          No actions. This extension cannot be run from a task.
                        </p>
                      )}
                    </div>

                    {ext.failures.length > 0 && (
                      <div className="grid border-t border-line gap-2 min-w-0 pt-3 border-t-(--settings-line)">
                        <h3 className="flex flex-wrap items-center gap-2 mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) text-[11px] font-bold tracking-[.08em] uppercase">
                          Recent failures{" "}
                          <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
                            {ext.failures.length}
                          </span>
                        </h3>
                        <ul className="grid list-none gap-1.5 min-w-0 mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0">
                          {ext.failures.slice(0, 5).map((failure, index) => (
                            <li
                              className="flex flex-wrap items-center border border-line grid gap-1 min-w-0 pt-2 pr-2.5 pb-2 pl-2.5 border-solid border-(--settings-line) border-l-2 border-l-(--settings-danger) rounded-lg bg-(--settings-card) text-[11px]"
                              key={`${ext.id}-failure-${failure.at}-${index}`}
                            >
                              <span className="flex flex-wrap items-center text-xs gap-2 text-(--settings-muted)">
                                <code className="min-w-0 break-all text-xs text-(--settings-ink) font-[ui-monospace,SFMono-Regular,Menlo,monospace]">
                                  {failure.kind}
                                </code>
                                {failure.key && <span>{failure.key}</span>}
                                {typeof failure.httpStatus === "number" && (
                                  <span>HTTP {failure.httpStatus}</span>
                                )}
                                <time dateTime={failure.at}>
                                  {formatDate(failure.at)}
                                </time>
                              </span>
                              {failure.message && (
                                <p className="text-sm mt-0 mr-0 mb-0 ml-0 wrap-anywhere text-(--settings-ink) leading-normal">
                                  {failure.message}
                                </p>
                              )}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {isEditing && (
                      <form
                        className="grid border border-dashed border-line gap-3 min-w-0 mt-0.5 mr-0 mb-0.5 ml-0 pt-3.5 pr-3.5 pb-3.5 pl-3.5 border-(--settings-line) rounded-[11px] bg-(--settings-subtle)"
                        onSubmit={saveEdit}
                        noValidate
                      >
                        <label className="grid gap-1.5 text-[12px] font-[550] text-foreground min-w-0">
                          Manifest JSON for {ext.key}
                          <textarea
                            className="resize-y border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 font-[ui-monospace,SFMono-Regular,Menlo,monospace] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-(--settings-line) bg-(--settings-card) text-(--settings-ink) [@media_(max-width:_700px)]:text-[16px]! w-full min-h-42.5 pt-3 pr-3.25 pb-3 pl-3.25 border-solid rounded-[10px] text-[12px] leading-[1.6]"
                            aria-label={`Manifest JSON for ${ext.key}`}
                            rows={12}
                            spellCheck={false}
                            value={editor.text}
                            onChange={(e) =>
                              setEditor({
                                id: ext.id,
                                text: e.target.value,
                                error: "",
                              })
                            }
                          />
                        </label>
                        {editor.error && (
                          <p
                            className="block text-muted mt-0 mr-0 mb-0 ml-0 text-[12px]"
                            role="alert"
                          >
                            {editor.error}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-2.25">
                          <button
                            className="inline-flex items-center justify-center border border-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-lg bg-(--settings-accent-strong) text-(--settings-on-accent) pt-0 pr-4 pb-0 pl-4 font-[620] shadow-[0_1px_2px_#24242412] hover:filter-[brightness(1.08)] min-h-9 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--settings-accent-strong)_88%,var(--settings-ink))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] [html[data-theme=dark]_&]:text-[#171719] justify-self-start font-[inherit] h-9 min-w-30 focus-visible:outline-solid focus-visible:outline-(--settings-accent) border-solid border-(--settings-accent-strong) text-[12px] [@media_(max-width:_700px)]:flex-[1_1_100%] [@media_(max-width:_700px)]:min-h-10 [@media_(max-width:_700px)]:justify-center"
                            disabled={saving}
                          >
                            {saving ? "Saving…" : "Save manifest"}
                          </button>
                          <button
                            type="button"
                            className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-0 pr-4 pb-0 pl-4 font-[inherit] h-9 min-w-30 justify-self-start focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] [@media_(max-width:_700px)]:flex-[1_1_100%] [@media_(max-width:_700px)]:min-h-10 [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                            onClick={() => setEditor(null)}
                          >
                            Cancel
                          </button>
                          <small className="block mt-0 mr-0 mb-0 ml-0 text-(--settings-muted) text-[11px] leading-[1.55]">
                            Only the difference is applied, in one transaction.
                          </small>
                        </div>
                      </form>
                    )}

                    <div className="flex flex-wrap items-center gap-2.25 min-w-0">
                      {!isEditing && (
                        <button
                          type="button"
                          className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] [@media_(max-width:_700px)]:flex-auto [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                          onClick={() => startEdit(ext)}
                        >
                          Edit manifest
                        </button>
                      )}
                      {isConfirming ? (
                        <div
                          className="flex w-full flex-wrap items-center border border-line basis-full gap-2.5 text-[12px] text-foreground pt-3 pr-3 pb-3 pl-3 border-solid border-[color-mix(in_srgb,var(--settings-danger)_24%,var(--settings-line))] rounded-[9px] bg-[color-mix(in_srgb,var(--settings-danger)_5%,var(--settings-card))] flex-[1_1_100%]"
                          role="group"
                          aria-label={`Confirm uninstall ${ext.key}`}
                        >
                          <span className="flex-[1_1_320px] leading-normal">
                            Uninstall “{ext.key}”? Its bot and credentials are
                            revoked, its hooks and actions stop, and pending
                            deliveries are deleted. Action history is kept.
                          </span>
                          <button
                            type="button"
                            className="border border-line disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 font-[inherit] bg-(--settings-danger) text-(--settings-card) border-0 rounded-md pt-1.5 pr-2.5 pb-1.5 pl-2.5 disabled:cursor-not-allowed w-auto min-h-7.5 text-[12px] focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-(--settings-danger) [@media_(max-width:_700px)]:flex-[1_1_100%] [@media_(max-width:_700px)]:justify-center"
                            disabled={busy}
                            onClick={() => void uninstall(ext)}
                          >
                            {busy ? "Uninstalling…" : "Uninstall"}
                          </button>
                          <button
                            type="button"
                            className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-0 pr-3 pb-0 pl-3 font-[inherit] w-auto min-h-8 text-[11px] focus-visible:outline-solid focus-visible:outline-(--settings-accent) border-(--settings-line) text-(--settings-ink) [@media_(max-width:_700px)]:flex-[1_1_100%] [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                            onClick={() => setConfirmId(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] [@media_(max-width:_700px)]:flex-auto [@media_(max-width:_700px)]:justify-center shell-phone:min-w-11! shell-phone:min-h-11!"
                          onClick={() => {
                            setConfirmId(ext.id);
                            setNotice(null);
                          }}
                        >
                          Uninstall
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}
