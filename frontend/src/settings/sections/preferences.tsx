import { Monitor, Moon, Sun } from "lucide-react";
import { RadioGroup as RadioPrimitive } from "radix-ui";
import { Segmented } from "../../components/ui/switch";
import { useSettings } from "../context";
import { SettingRow, SettingsPage, SettingsSection, useSetting } from "../ui";

/* Reference implementation for a settings section: composites only, one useSetting per
 * value, the control applies instantly and the row reports Saving → Saved or the error. */
const THEMES = [
  {
    value: "light",
    label: "Light",
    icon: Sun,
    swatch: "bg-neutral-100",
    bar: "bg-neutral-300",
    side: "bg-neutral-500",
  },
  {
    value: "dark",
    label: "Dark",
    icon: Moon,
    swatch: "bg-neutral-900",
    bar: "bg-neutral-600",
    side: "bg-neutral-200",
  },
  {
    value: "system",
    label: "System",
    icon: Monitor,
    swatch: "bg-linear-to-r from-neutral-100 from-50% to-neutral-900 to-50%",
    bar: "bg-neutral-400",
    side: "bg-neutral-500",
  },
] as const;

export default function PreferencesSettings() {
  const { theme, fontScale, savePreference } = useSettings();
  const themeSetting = useSetting({
    value: theme,
    save: (next) => savePreference("theme", next),
  });
  const fontSetting = useSetting({
    value: fontScale,
    save: (next) => savePreference("fontSize", next),
  });

  return (
    <SettingsPage
      title="Preferences"
      description="How Hoja looks for you. Saved to your account, so it follows you to every device."
    >
      <SettingsSection title="Appearance">
        <SettingRow
          label="Theme"
          description="System follows your device and switches with it."
          status={themeSetting.status}
          stacked
        >
          <RadioPrimitive.Root
            aria-label="Theme"
            value={themeSetting.value}
            onValueChange={(v) => void themeSetting.set(v)}
            className="grid w-full grid-cols-1 gap-3 sm:grid-cols-3"
            data-theme-control=""
          >
            {THEMES.map((t) => (
              <RadioPrimitive.Item
                key={t.value}
                value={t.value}
                className="group grid overflow-hidden rounded-lg border border-line bg-card text-left outline-none hover:border-ring/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring state-checked:border-primary state-checked:ring-1 state-checked:ring-primary"
              >
                <span
                  aria-hidden="true"
                  className={`grid h-20 grid-cols-4 gap-2 p-3 ${t.swatch}`}
                >
                  <span className={`row-span-3 rounded-sm ${t.side}`} />
                  <span className={`col-span-3 h-2 rounded-sm ${t.bar}`} />
                  <span className={`col-span-2 h-2 rounded-sm ${t.bar}`} />
                  <span
                    className={`col-span-3 h-2 rounded-sm opacity-60 ${t.bar}`}
                  />
                </span>
                <span className="flex items-center gap-2 px-3 py-2.5 text-base font-medium">
                  <t.icon
                    className="size-3.5 text-muted-foreground"
                    aria-hidden="true"
                  />
                  {t.label}
                  <span
                    className="ml-auto size-3.5 rounded-full border border-input group-data-[state=checked]:border-4 group-data-[state=checked]:border-primary"
                    aria-hidden="true"
                  />
                </span>
              </RadioPrimitive.Item>
            ))}
          </RadioPrimitive.Root>
        </SettingRow>
        <SettingRow
          label="Text size"
          description="Scales text across the whole app."
          status={fontSetting.status}
        >
          <Segmented
            label="Text size"
            value={fontSetting.value}
            onValueChange={(v) => void fontSetting.set(v)}
            options={[
              { value: "90", label: "Compact" },
              { value: "100", label: "Default" },
              { value: "110", label: "Large" },
            ]}
          />
        </SettingRow>
      </SettingsSection>
    </SettingsPage>
  );
}
