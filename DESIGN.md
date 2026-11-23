# Design System

## Core principles

- After completing any task that modifies a `.tsx` file, run `npx prettier --write <modified-file-paths>` from `app/frontend` as the final step, passing only the files modified during that task (for example, `npx prettier --write src/main.tsx`). Do not format the whole codebase or unrelated files.

- Prefer inline Tailwind utility classes in markup over writing styles in CSS stylesheets. Add or change stylesheet rules only when utilities cannot reasonably express the required styling.
- Build responsive layouts that adapt naturally to the available space.
- Prefer flex layouts for alignment and distribution. Center content with `flex justify-center`, adding `items-center` when vertical centering is needed.
- Prefer flexible sizing and max-width constraints. Use max-height constraints only when they serve a clear responsive need.
- Avoid hard-coded widths and heights; let content and responsive layout determine dimensions.
- Use `p-4` as the default padding utility.

## Typography

- Use Tailwind’s default font-size scale only. Do not define custom font sizes or use arbitrary font-size values.
- Use the regular/base text size as the default; do not enlarge text by default.
- Do not render text in full uppercase.
- Do not use eyebrow headings or eyebrow-style subheadings.

## Shape and spacing

- Use standard Tailwind spacing and sizing utilities. Do not use arbitrary values in square brackets, including pixel values such as `w-[10px]`.
- Do not introduce custom pixel values or custom Tailwind utility values.

## State shape

An interactive surface keeps the same shape in every state. The pill a control paints on hover, selected or pressed, and its focus ring, must never be cut by an ancestor that clips (`overflow-hidden`, `overflow-x-hidden`, or any `overflow-auto` scroller at its first or last row). A cut surface reads as a pill rounded on one side and square on the other, or a focus ring with a missing edge.

- Never combine `w-full` with a horizontal margin (`mx-*`, `ml-*`, `mr-*`). The box becomes wider than its parent and the far edge is clipped. Inset with margins and let a block or `flex flex-col` parent stretch the box, or keep `w-full` and inset with padding. `npm run lint:state-shape` enforces this.
- A row that fills its container (sidebar rows, list rows in a clipped card, a full-bleed leaderboard row) draws its focus ring inside itself: `focus-visible:-outline-offset-2`.
- A free-standing control keeps the outer ring (`focus-visible:outline-2 focus-visible:outline-offset-2`), and its scroller leaves room for it. A horizontal tab strip that scrolls takes `-m-1 p-1`.
- On touch (`shell-phone:` / `pointer-coarse:`), a control grown to a 44px target must fit its row: grow the row too (`shell-phone:h-11 pointer-coarse:h-11`), or keep the 44px box invisible and paint hover and focus on an inner element (`group-hover:` / `group-focus-visible:`, as `MobileNavTrigger` does).
- `npm run test:state-shape` drives the app in light, dark, 125% zoom and phone sizes. It hovers with the real pointer (no scroll-into-view) and focuses with `preventScroll`, then fails on any clipped hover or focus paint.

## Main content container

Use this shell for full-height issue surfaces displayed beside the sidebar.

- The outer `.main` is the gap surface: `background: var(--sidebar-bg)` and `border-radius: 0`. Do not round it.
- The inner content shell uses `mt-2 mr-2 mb-2` / `margin: .5rem .5rem .5rem 0` — 8px top, right, and bottom, with no left margin.
- The content shell uses `rounded-xl` (`border-radius: .75rem`; 10.5px with the current 14px root size).
- The content shell border uses `border-[0.5px] border-neutral-300/70`, which renders as `1px solid rgb(212 212 212 / 70%)` at 100% zoom.
- The shell background is `var(--bg)` with `overflow: hidden`.
- Keep the shell full-height with `display: flex; flex-direction: column; flex: 1; min-height: 0`.
- The exposed 8px gaps reveal `var(--sidebar-bg)`; they are outside the content surface.
- Internal dividers and control borders use `1px solid var(--line)`.

```tsx
<div className="team-issues-page mt-2 mr-2 mb-2 flex min-h-0 flex-1 flex-col rounded-xl border-[0.5px] border-neutral-300/70">
```

```css
.workspace-content:has(.team-issues-page) .main{background:var(--sidebar-bg);border-radius:0}
.team-issues-page{background:var(--bg);overflow:hidden}
```

Theme tokens:

- `--sidebar-bg`: `#f1f1f1` light / `#242427` dark
- `--bg`: `#f9f9fa` light / `#171719` dark
- `--line`: `#e5e5e7` light / `#3a3a40` dark
- Shell border: `neutral-300` (`#d4d4d4`) at 70% opacity

Content inside the shell may add its own 16px inset, such as the issue board's `p-4`; that inner inset is separate from the shell spacing above.

## Responsive implementation

- Prefer responsive Tailwind utilities and content-driven sizing over fixed dimensions.
- Use flexbox and max-width constraints to keep layouts readable across screen sizes; avoid fixed widths and heights.
- Do not use arbitrary-value utilities such as `h-[...]`, `max-w-[...]`, or `text-[...]`.

## Animation

- Animate `transform` properties instead of layout properties such as `width`, `height`, or `left` whenever possible. Layout properties can trigger layout recalculation on every frame and stutter when the main thread is busy; transforms avoid layout and generally stay smooth.
- For color transparency animations, prefer animating the alpha channel in an RGBA color rather than animating `opacity`, when possible.
- For chips whose styling changes with active or selected state, ease out when entering the active state but do not ease in when leaving it. Apply the transition only on the active state, or keep the base transition off, so deselection is immediate.
- Hover styling states snap in: do not ease in. They ease out when the pointer leaves, so the target reads as instantly responsive and the highlight dissolves afterwards. Declare the transition on the base style only and set `transition: none` on the `:hover` rule.

```css
.row{transition:background-color 120ms ease-out} /* leaving */
.row:hover{background:#e5e5e5;transition:none}     /* entering */
```

This is the inverse of the active/selected rule above, which is deliberate: hover is feedback on the pointer, selection is feedback on committed state.

## Popovers and menus

- Every popover, dropdown and menu dismisses through `Popover` or `useMenuDismiss` from `frontend/src/menu.tsx`. They close on an outside press and on Escape. Do not add ad-hoc `document` listeners for outside clicks.
- A panel rendered outside its trigger's DOM subtree, such as through a portal, must pass its ref to `useMenuDismiss` through `alsoWithin` (or include it in `within`) so presses inside the panel do not dismiss it.
- Popover triggers expose `aria-haspopup`, `aria-expanded` and `aria-controls`, and the panel uses the matching `role` and `id`.
- Position portaled panels with fixed coordinates and clamp them to the viewport so scroll containers cannot clip them.

## Settings

Every settings screen is composed from two layers and nothing else (enforced by `local/settings-primitives-only` in `src/settings/sections/`):

- `src/components/ui/`: primitives built on `radix-ui` (`Button`, `Input`, `Textarea`, `Label`, `Switch`, `Checkbox`, `RadioGroup`, `Segmented`, `Select`, `Dialog`, `AlertDialog`, `DropdownMenu`, `Tooltip`, `toast`). Radix layers close on an outside press and on Escape themselves, which satisfies the popover rule above.
- `src/settings/ui/`: settings composites (`SettingsPage`, `SettingsSection`, `SettingRow`, `ResourceRow`, `Loadable`, `EmptyState`, `ErrorState`, `Notice`, `Badge`, `CopyField`, `FormDialog`, `Field`, `ConfirmAction`, `DangerZone`) and the save hooks (`useSetting`, `useSettingText`, `useResource`, `useAction`).

Sections are listed once, in `src/settings/registry.ts`. The navigation, the router and the browser suite read that list. A section is one lazily loaded file in `src/settings/sections/` that reads the shell through `useSettings()`.

The settings navigation is not a second sidebar. It renders inside the app's single `<aside id="workspace-sidebar">`, using the shared `SidebarItem` and `SidebarGroup` from `src/components/sidebar.tsx`.

### Saving

| Control | When it saves | Feedback |
| --- | --- | --- |
| Switch, segmented control, radio cards, select | Immediately, optimistically | Inline `Saving…` then `Saved`. On failure the control snaps back and the row shows the reason with Retry. |
| Text and number fields | On blur or Enter; Escape reverts | Validation runs before the request; the error sits under the field. |
| Several fields that only make sense together (a policy, a provider) | One explicit primary button per form | The button shows its busy state; the form keeps its values on failure. |
| Create or edit a list item | `FormDialog` | The dialog closes only after the server succeeded. |

- The latest change wins. A slow earlier response never overwrites a later choice (`useSetting` sequences requests).
- A workspace-wide change may offer Undo through `undoLabel`. Routine personal changes do not toast.
- A settings screen never mixes instant controls and a Save button for the same object.

### Destructive and secret actions

- Anything that deletes, revokes, disconnects, removes a person or starts work for many tasks goes through `ConfirmAction`, which names the object and the consequence.
- If the action cannot be undone and affects other people (deleting a workspace or a team, revoking every session), the person must type the object's name.
- Row actions live in one `⋯` menu (`ResourceRow actions`). Destructive items are last and use `destructive`.
- A secret is shown once, in a `CopyField`, with a sentence saying it will not be shown again. It is never re-rendered.

### Access

- Gate on what the API checks. Where the backend checks a capability (`team.create`, `member.invite`, `label.manage`, `extension.manage`, `task.delete`), use `can(capability)`. Where it checks admin, use `isAdmin`.
- Someone who may read but not change a setting sees the value disabled, with a `locked` reason on the row. Do not hide it.
- Admin-only sections are marked `admin` in the registry. The router shows "Only workspace admins can open this page" for everyone else.

### States and copy

- Every remote read goes through `Loadable`: skeleton rows while loading, `ErrorState` with Try again, and an `EmptyState` that names the next action.
- Sentence case everywhere. Errors say what happened and what to do next. Never show server internals (hosts, stack traces, SQL).
- Use `text-base` for labels and `text-sm` for descriptions. Page and section titles use the measured `--ds-settings-*` tokens.
