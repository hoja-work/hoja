import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { SettingsSectionId } from "../registry";

/* One lazily loaded module per settings section: src/settings/sections/<section-id>.tsx with a
 * default export. Files are discovered, so adding a section never edits a shared file. Helpers
 * for a section live in a sub-folder (sections/<id>/...), which this glob does not pick up. */
const modules = import.meta.glob<{ default: ComponentType }>("./*.tsx");

export const SECTION_COMPONENTS: Partial<
  Record<SettingsSectionId, LazyExoticComponent<ComponentType>>
> = Object.fromEntries(
  Object.entries(modules).map(([path, load]) => [
    path.slice(2, -".tsx".length),
    lazy(load),
  ]),
);
