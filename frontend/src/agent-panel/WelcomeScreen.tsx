import {
  Activity,
  Bug,
  CalendarClock,
  ListTodo,
  Sparkles,
} from "lucide-react";

interface StarterItem {
  title: string;
  subtitle: string;
  prompt: string;
  icon: typeof ListTodo;
}

const STARTERS: StarterItem[] = [
  {
    title: "Summarize my open tasks",
    subtitle: "Review assigned work and highlight priorities",
    prompt: "Summarize my open tasks.",
    icon: ListTodo,
  },
  {
    title: "What is in progress this cycle?",
    subtitle: "Check active issues and blockers in the cycle",
    prompt: "What is in progress in the active cycle?",
    icon: Activity,
  },
  {
    title: "What is due this week?",
    subtitle: "Surface upcoming deadlines across teams",
    prompt: "What tasks are due this week?",
    icon: CalendarClock,
  },
  {
    title: "Find unassigned bugs",
    subtitle: "Locate untriaged bug reports needing an owner",
    prompt: "Find unassigned bugs and list them.",
    icon: Bug,
  },
];

export function WelcomeScreen({
  workspaceName,
  onSelectPrompt,
  disabled = false,
}: {
  workspaceName: string;
  onSelectPrompt: (prompt: string) => void;
  disabled?: boolean;
}) {
  const name = workspaceName || "Hoja";
  return (
    <div
      className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center justify-center px-4 py-8 sm:py-12"
      data-agent-welcome=""
    >
      <div className="flex size-11 items-center justify-center rounded-2xl border border-line bg-panel shadow-xs">
        <Sparkles className="size-5 text-foreground" aria-hidden="true" />
      </div>
      <h1 className="mt-4 mb-1 text-center text-xl font-semibold tracking-tight text-foreground">
        Ask the {name} agent
      </h1>
      <p className="m-0 max-w-md text-center text-sm text-muted">
        Search issues, inspect cycles, update tasks, or delegate work to an
        agent. Tool activity stays neatly collapsed until you want the details.
      </p>

      <div className="mt-6 grid w-full grid-cols-1 gap-2.5 sm:grid-cols-2">
        {STARTERS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.title}
              type="button"
              disabled={disabled}
              onClick={() => onSelectPrompt(item.prompt)}
              className="group flex items-start gap-3 rounded-xl border border-line bg-panel p-3.5 text-left transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg border border-line bg-bg text-muted">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="text-sm font-medium text-foreground">
                  {item.title}
                </span>
                <span className="text-xs text-muted">{item.subtitle}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
