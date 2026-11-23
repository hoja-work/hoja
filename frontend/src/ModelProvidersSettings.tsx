import React, { useEffect, useState } from "react";
import { confirmDialog } from "./dialogs";

type ModelProvider = {
  id: string;
  name: string;
  provider: string;
  modelId: string;
  baseUrl: string | null;
  contextWindow: number | null;
  managed: boolean;
  enabled: boolean;
  hasApiKey: boolean;
};

// baseUrl is intentionally not requested: for a managed provider it is an internal
// address that must never reach the browser.
const LIST =
  "query ModelProviders { modelProviders { id name provider modelId contextWindow enabled hasApiKey managed } }";
const CREATE =
  "mutation($name:String!,$provider:String!,$modelId:String!,$baseUrl:String,$apiKey:String!){modelProviderCreate(name:$name,provider:$provider,modelId:$modelId,baseUrl:$baseUrl,apiKey:$apiKey){id}}";
const UPDATE =
  "mutation($id:String!,$enabled:Boolean){modelProviderUpdate(id:$id,enabled:$enabled){id enabled}}";
const DELETE = "mutation($id:String!){modelProviderDelete(id:$id)}";

async function call(query: string, variables: Record<string, unknown> = {}) {
  const token = localStorage.getItem("hoja_token");
  const res = await fetch("/graphql", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token && token !== "session"
        ? { authorization: `Bearer ${token}` }
        : {}),
    },
    credentials: "include",
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  if (body.errors?.length) throw new Error(body.errors[0].message);
  return body.data;
}

export function ModelProvidersSettings() {
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("openai");
  const [modelId, setModelId] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let live = true;
    call(LIST)
      .then((d) => live && setProviders(d.modelProviders))
      .catch((e) => live && setMessage((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [refresh]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      await call(CREATE, {
        name,
        provider,
        modelId,
        baseUrl: baseUrl || null,
        apiKey,
      });
      setName("");
      setModelId("");
      setApiKey("");
      setMessage(
        "Model added. The key is stored encrypted and cannot be viewed again.",
      );
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (p: ModelProvider) => {
    try {
      await call(UPDATE, { id: p.id, enabled: !p.enabled });
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
    }
  };

  const remove = async (p: ModelProvider) => {
    if (
      !(await confirmDialog(
        `Delete ${p.name}? The agent will stop using this model.`,
        { danger: true },
      ))
    )
      return;
    try {
      await call(DELETE, { id: p.id });
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
    }
  };

  return (
    <section
      className="w-[min(100%,var(--ds-settings-column-width))] mt-0 mr-auto mb-0 ml-auto pt-(--ds-settings-column-padding) pr-(--ds-settings-column-padding) pb-16 pl-(--ds-settings-column-padding) font-(family-name:--hoja-font-ui) [font-optical-sizing:auto] min-w-0 shell-phone:pt-0 text-(--settings-ink) max-w-(--ds-settings-column-width) [@media_(max-width:_700px)]:pt-0 [html[data-theme=dark]_&]:bg-transparent [@media_(min-width:_881px)]:pl-(--ds-settings-column-padding) [@media_(min-width:_881px)]:pr-(--ds-settings-column-padding) [--settings-accent:#171717] [--settings-accent-strong:#171717] [--settings-accent-soft:#e9e9e9] [--settings-ink:#171717] [--settings-muted:#626262] [--settings-line:#dcdcdc] [--settings-card:#fff] [--settings-subtle:#f0f0f0] [--settings-on-accent:#fff] [--settings-danger:#242424] [--settings-success:#242424] [html[data-theme=dark]_&]:[--settings-accent:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-strong:#f2f2f2] [html[data-theme=dark]_&]:[--settings-accent-soft:#2c2c2c] [html[data-theme=dark]_&]:[--settings-on-accent:#171717] [html[data-theme=dark]_&]:[--settings-ink:#f2f2f2] [html[data-theme=dark]_&]:[--settings-muted:#aaa] [html[data-theme=dark]_&]:[--settings-line:#353535] [html[data-theme=dark]_&]:[--settings-card:#1b1b1b] [html[data-theme=dark]_&]:[--settings-subtle:#262626] [html[data-theme=dark]_&]:[--settings-danger:#eee] [html[data-theme=dark]_&]:[--settings-success:#d8d8d8]"
      data-settings-page=""
    >
      <header className="mt-0 mr-0 mb-8 ml-0 [@media_(max-width:_700px)]:mb-6.75 pb-0 border-b-0 [@media_(max-width:_700px)]:pb-5">
        <h1 className="text-(length:--ds-settings-title-size) tracking-[-.035em] mt-0 mr-0 mb-2 ml-0 font-(--ds-settings-title-weight) leading-(--ds-settings-title-line) text-(--settings-ink) font-(family-name:--hoja-font-ui) [@media_(max-width:_700px)]:text-(length:--ds-settings-title-size)">
          Model providers
        </h1>
        <p className="text-(--settings-muted) text-[13px] leading-[1.6] wrap-anywhere max-w-full mt-0 mr-0 mb-0 ml-0 [@media_(max-width:_700px)]:text-[13px]">
          Models available to the workspace agent. Users never supply their own
          keys.
        </p>
      </header>
      <div className="border border-line sm:p-6 mt-0 mr-0 mb-9 ml-0 pt-7.5 pr-7.5 pb-7.5 pl-7.5 border-0 rounded-[10px] bg-[#171717] [--settings-ink:#f2f2f2] [--settings-muted:#aaa] shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-5.5 [@media_(max-width:_700px)]:pr-4.75 [@media_(max-width:_700px)]:pb-5.5 [@media_(max-width:_700px)]:pl-4.75 [@media_(max-width:_700px)]:rounded-[13px] [html[data-theme=dark]_&]:border-[#383838] [html[data-theme=dark]_&]:bg-[#111]">
        <h2 className="text-[23px] mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)">
          Add a model provider
        </h2>
        <p className="text-[13px]! leading-[1.55]! text-(--settings-muted)! mt-1.5! mr-0! mb-5! ml-0! max-w-147.5 bg-transparent">
          Connect an OpenAI-compatible model. Credentials are encrypted and
          cannot be viewed again.
        </p>
        <form
          className="grid sm:grid-cols-2 gap-3.5 max-w-none w-full grid-cols-2 [@media_(max-width:_700px)]:grid-cols-[minmax(0,1fr)] [@media_(max-width:_700px)]:gap-3.5"
          onSubmit={create}
        >
          <label className="grid min-w-0 gap-1.5 text-[12px] font-[550] text-(--settings-ink) bg-transparent">
            Display name
            <input
              className="w-full border border-line text-base sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-white text-[#171717] font-[inherit] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-[#d4d4d4] [@media_(max-width:_700px)]:text-[16px]! [html[data-theme=dark]_&]:border-[#d4d4d4] [html[data-theme=dark]_&]:bg-white [html[data-theme=dark]_&]:text-[#171717]"
              aria-label="Model display name"
              required
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="grid min-w-0 gap-1.5 text-[12px] font-[550] text-(--settings-ink) bg-transparent">
            Provider
            <input
              className="w-full border border-line text-base sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-white text-[#171717] font-[inherit] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-[#d4d4d4] [@media_(max-width:_700px)]:text-[16px]! [html[data-theme=dark]_&]:border-[#d4d4d4] [html[data-theme=dark]_&]:bg-white [html[data-theme=dark]_&]:text-[#171717]"
              aria-label="Provider"
              required
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            />
          </label>
          <label className="grid min-w-0 gap-1.5 text-[12px] font-[550] text-(--settings-ink) bg-transparent">
            Model ID
            <input
              className="w-full border border-line text-base sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-white text-[#171717] font-[inherit] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-[#d4d4d4] [@media_(max-width:_700px)]:text-[16px]! [html[data-theme=dark]_&]:border-[#d4d4d4] [html[data-theme=dark]_&]:bg-white [html[data-theme=dark]_&]:text-[#171717]"
              aria-label="Model ID"
              required
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
            />
          </label>
          <label className="sm:col-span-2 grid gap-1.5 text-[12px] font-[550] text-(--settings-ink) col-span-full [@media_(max-width:_700px)]:col-auto bg-transparent">
            Base URL (OpenAI-compatible)
            <input
              className="w-full border border-line text-base sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-white text-[#171717] font-[inherit] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-[#d4d4d4] [@media_(max-width:_700px)]:text-[16px]! [html[data-theme=dark]_&]:border-[#d4d4d4] [html[data-theme=dark]_&]:bg-white [html[data-theme=dark]_&]:text-[#171717]"
              aria-label="Base URL"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </label>
          <label className="grid min-w-0 gap-1.5 text-[12px] font-[550] text-(--settings-ink) bg-transparent">
            API key
            <input
              className="w-full border border-line text-base sm:text-sm shell-phone:min-h-11 pointer-coarse:min-h-11 pt-2.25 pr-2.25 pb-2.25 pl-2.25 border-solid rounded-[7px] bg-white text-[#171717] font-[inherit] min-w-0 max-w-full focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-(--settings-accent) focus-visible:outline-offset-2 border-[#d4d4d4] [@media_(max-width:_700px)]:text-[16px]! [html[data-theme=dark]_&]:border-[#d4d4d4] [html[data-theme=dark]_&]:bg-white [html[data-theme=dark]_&]:text-[#171717]"
              aria-label="API key"
              type="password"
              required
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
            />
          </label>
          <div className="flex flex-wrap items-center sm:col-span-2 col-span-full gap-3.5 mt-4.5 [@media_(max-width:_700px)]:col-auto [@media_(max-width:_700px)]:items-start [@media_(max-width:_700px)]:flex-col [@media_(max-width:_700px)]:gap-2.25">
            <button
              className="inline-flex items-center justify-center border border-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-lg bg-white text-[#171717] pt-0 pr-4 pb-0 pl-4 font-[620] shadow-[0_1px_2px_#24242412] hover:filter-[brightness(1.08)] min-h-9 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[#e8e8e8] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] [html[data-theme=dark]_&]:text-[#171719] justify-self-start font-[inherit] h-9 min-w-30 focus-visible:outline-solid focus-visible:outline-(--settings-accent) border-solid border-(--settings-accent-strong) text-[12px] mt-0 mr-0 mb-0 ml-0 border-white"
              disabled={busy || !name.trim() || !modelId.trim() || !apiKey}
            >
              {busy ? "Saving…" : "Add model"}
            </button>
            <span className="text-(--settings-muted) text-[11px] leading-[1.45]">
              Stored securely for workspace use.
            </span>
          </div>
        </form>
        {message && (
          <p
            className="m-0 text-[13px] leading-[1.6] text-muted"
            role={message.startsWith("Model added") ? "status" : "alert"}
          >
            {message}
          </p>
        )}
      </div>
      <div className="mt-0 mr-0 mb-9 ml-0 pt-0 pr-0 pb-0 pl-0 border-0 rounded-none bg-transparent shadow-none min-w-0 shell-phone:pt-3.75 shell-phone:pr-3.75 shell-phone:pb-3.75 shell-phone:pl-3.75 w-full max-w-none text-(--settings-ink) [@media_(max-width:_700px)]:mb-7 [@media_(max-width:_700px)]:pt-4.75 [@media_(max-width:_700px)]:pr-4.25 [@media_(max-width:_700px)]:pb-4.75 [@media_(max-width:_700px)]:pl-4.25 [@media_(max-width:_700px)]:rounded-[13px]">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-5.25 [@media_(max-width:_390px)]:items-start">
          <div>
            <h2 className="text-(length:--ds-settings-section-size) mt-0 mr-0 mb-0 ml-0 text-(--settings-ink) font-(family-name:--hoja-font-ui) font-(--ds-settings-section-weight) tracking-[-.035em] leading-(--ds-settings-section-line)">
              Provider inventory
            </h2>
          </div>
          <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
            {providers.length} configured
          </span>
        </div>
        <div className="overflow-hidden border-t border-line border-0 rounded-none bg-panel border-(--settings-line) shadow-[0_1px_2px_#1b1b1b08] min-w-0 max-w-full mt-3.5 border-t-(--settings-line)">
          {providers.map((p) => (
            <div
              className="flex flex-wrap items-center border-b border-line gap-2.5 w-full pt-5 pr-1 pb-5 pl-1 border-b-line bg-transparent text-left last:border-b-0 hover:bg-transparent min-h-18.5 min-w-0 border-t border-t-line border-(--settings-line)"
              key={p.id}
            >
              <span className="flex flex-1 flex-col grid gap-1 flex-[1_1_100%] min-w-0">
                <span className="flex flex-wrap items-center gap-2.25">
                  <b className="text-foreground font-[550] block min-w-0 wrap-anywhere text-[14px]">
                    {p.name}
                  </b>
                  <span
                    className={
                      `inline-flex items-center border border-line whitespace-nowrap ${p.enabled ? "" : ""}` +
                      " inline-flex items-center w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold whitespace-nowrap"
                    }
                  >
                    {p.enabled ? "Enabled" : "Disabled"}
                  </span>
                </span>
                <small className="leading-relaxed text-(--settings-muted) text-[12px] block min-w-0 wrap-anywhere gap-[6px_12px] flex-wrap mt-1.75">
                  {p.managed && (
                    <span className="inline-flex items-center border border-line whitespace-nowrap w-fit min-h-6.25 pt-0.5 pr-2 pb-0.5 pl-2 border-solid border-(--settings-line) rounded-[999px] bg-(--settings-subtle) text-(--settings-muted) text-[11px] font-semibold">
                      Internal · managed by Hoja
                    </span>
                  )}
                  <span>{p.provider}</span>
                  <code className="min-w-0 break-all text-xs wrap-anywhere text-(--settings-accent-strong) font-(family-name:--hoja-font-ui)">
                    {p.modelId}
                  </code>
                  {p.contextWindow && (
                    <span>{Math.round(p.contextWindow / 1000)}k context</span>
                  )}
                  <span>
                    {p.hasApiKey ? "Encrypted key stored" : "No key stored"}
                  </span>
                </small>
              </span>
              <button
                type="button"
                className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] flex-none focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-ink) text-[11px] shell-phone:min-w-11! shell-phone:min-h-11!"
                onClick={() => void toggle(p)}
              >
                {p.enabled ? "Disable" : "Enable"}
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center border border-line hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 hover:bg-(--settings-subtle) flex gap-1.75 border-solid rounded-lg bg-(--settings-card) pt-2 pr-2.75 pb-2 pl-2.75 font-[inherit] flex-none focus-visible:outline-solid focus-visible:outline-(--settings-accent) min-h-8 border-(--settings-line) text-(--settings-danger) text-[11px] shell-phone:min-w-11! shell-phone:min-h-11!"
                onClick={() => void remove(p)}
              >
                Delete
              </button>
            </div>
          ))}
          {loading && (
            <p
              role="status"
              className="grid justify-items-center text-center text-sm gap-1.75 pt-7.5! pr-4.5! pb-7.5! pl-4.5! text-(--settings-muted)"
            >
              Loading model providers…
            </p>
          )}
          {!loading && !providers.length && (
            <div className="grid justify-items-center text-center text-sm gap-1.75 pt-7.5! pr-4.5! pb-7.5! pl-4.5! text-(--settings-muted)">
              <BotAvatar />
              <b className="font-semibold text-(--settings-ink) text-[12px]">
                No models configured
              </b>
              <p className="text-muted max-w-90 mt-0 mr-0 mb-0 ml-0 text-[11px] leading-[1.55]">
                Add a provider above to make a model available to the workspace
                agent.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function BotAvatar() {
  return (
    <span
      className="flex items-center justify-center grid place-items-center w-9 h-9 mb-0.75 rounded-[11px] bg-(--settings-accent-soft) text-(--settings-accent-strong) text-[12px] font-bold"
      aria-hidden="true"
    >
      AI
    </span>
  );
}
