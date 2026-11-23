import { Avatar, AvatarFallback } from "../../../components/ui/avatar";

export function initials(name?: string | null, email?: string | null) {
  const source = (name || email || "?").trim();
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
}

export function PersonAvatar({
  name,
  email,
}: {
  name?: string | null;
  email?: string | null;
}) {
  return (
    <Avatar aria-hidden="true">
      <AvatarFallback className="text-xs font-medium text-muted-foreground">
        {initials(name, email)}
      </AvatarFallback>
    </Avatar>
  );
}

export function TeamMark({ teamKey }: { teamKey: string }) {
  return (
    <span
      aria-hidden="true"
      className="grid size-8 flex-none place-items-center rounded-lg border border-line bg-subtle text-xs font-semibold text-muted-foreground"
    >
      {teamKey.slice(0, 3)}
    </span>
  );
}
