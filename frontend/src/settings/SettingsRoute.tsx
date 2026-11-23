import * as React from "react";
import { Lock, SearchX } from "lucide-react";
import { Button } from "../components/ui/button";
import { ToastHost } from "../components/ui/toast";
import { SettingsContext, type SettingsContextValue } from "./context";
import { canOpen, findSection } from "./registry";
import { SECTION_COMPONENTS } from "./sections";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  SettingsCard,
  SettingsPage,
} from "./ui";

class SectionBoundary extends React.Component<
  { children: React.ReactNode; resetKey: string },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error)
      this.setState({ error: null });
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <SettingsPage title="Something went wrong">
        <SettingsCard>
          <EmptyState
            title="This settings page hit an error"
            description="Nothing was changed. Reload to try again; if it keeps happening, the error below helps us find it."
            action={
              <Button onClick={() => window.location.reload()}>Reload</Button>
            }
          />
          <pre className="m-0 overflow-x-auto bg-subtle p-3 text-xs text-muted-foreground">
            {this.state.error.message}
          </pre>
        </SettingsCard>
      </SettingsPage>
    );
  }
}

/* Router for /workspace/settings/:section. Access rules live here once: a section marked
 * `admin` in the registry renders "Admins only" for everyone else, whatever the URL says. */
export function SettingsRoute({
  section,
  value,
}: {
  section: string;
  value: SettingsContextValue;
}) {
  const def = findSection(section);
  let body: React.ReactNode;
  if (!def) {
    body = (
      <SettingsPage title="Page not found">
        <SettingsCard>
          <EmptyState
            icon={<SearchX />}
            title={`There is no “${section}” settings page`}
            description="It may have moved. Pick a section from the list, or go back to Preferences."
            action={
              <Button onClick={() => value.openSection("preferences")}>
                Open Preferences
              </Button>
            }
          />
        </SettingsCard>
      </SettingsPage>
    );
  } else if (def.capability && !value.isAdmin && !value.capabilitiesReady) {
    body = (
      <SettingsPage title={def.label}>
        <LoadingRows rows={3} />
      </SettingsPage>
    );
  } else if (!canOpen(def, value.isAdmin, value.can)) {
    body = (
      <SettingsPage title={def.label}>
        <SettingsCard>
          <EmptyState
            icon={<Lock />}
            title={
              def.capability
                ? "You don't have access to this page"
                : "Only workspace admins can open this page"
            }
            description={
              def.capability
                ? "An admin can grant access under Settings → Permissions, or make the change for you."
                : "Ask an admin of this workspace if something here needs to change."
            }
            action={
              <Button onClick={() => value.openSection("preferences")}>
                Back to your settings
              </Button>
            }
          />
        </SettingsCard>
      </SettingsPage>
    );
  } else {
    const Section = SECTION_COMPONENTS[def.id];
    body = Section ? (
      <React.Suspense
        fallback={
          <SettingsPage title={def.label}>
            <LoadingRows rows={4} />
          </SettingsPage>
        }
      >
        <Section />
      </React.Suspense>
    ) : (
      <SettingsPage title={def.label}>
        <ErrorState error="This settings page is missing from the build." />
      </SettingsPage>
    );
  }
  return (
    <SettingsContext.Provider value={value}>
      <SectionBoundary resetKey={section}>
        <div data-settings-section-id={def?.id || section} className="contents">
          {body}
        </div>
      </SectionBoundary>
      <ToastHost />
    </SettingsContext.Provider>
  );
}
