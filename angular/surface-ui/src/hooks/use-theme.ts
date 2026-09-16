import { useEffect, useState, useCallback } from "react";

export type ThemeMode = "dark" | "light" | "steel" | "circuit";

export interface ThemeOption {
  id: ThemeMode;
  name: string;
  shortName: string;
  description: string;
  dotColor: string;
  isDarkBase: boolean;
}

export const THEME_OPTIONS: ThemeOption[] = [
  {
    id: "dark",
    name: "Dark Workbench",
    shortName: "Dark",
    description: "Deep ink-blue canvas with signal amber accents",
    dotColor: "bg-primary",
    isDarkBase: true,
  },
  {
    id: "light",
    name: "Light Precision",
    shortName: "Light",
    description: "Clean technical off-white canvas with high-contrast slate",
    dotColor: "bg-amber-600",
    isDarkBase: false,
  },
  {
    id: "steel",
    name: "Machined Steel",
    shortName: "Steel",
    description: "Industrial graphite & titanium with ice-blue accents",
    dotColor: "bg-slate-400",
    isDarkBase: true,
  },
  {
    id: "circuit",
    name: "Circuit (Hi-Con)",
    shortName: "Circuit",
    description: "Cybernetic high-contrast terminal with glowing teal",
    dotColor: "bg-emerald-400",
    isDarkBase: true,
  },
];

const STORAGE_KEY = "relics_workbench_theme";
const ALL_THEME_CLASSES: ThemeMode[] = ["dark", "light", "steel", "circuit"];

function applyThemeToDocument(theme: ThemeMode) {
  const root = document.documentElement;
  ALL_THEME_CLASSES.forEach((t) => root.classList.remove(t));
  root.classList.add(theme);
}

export function useTheme() {
  const [theme, setThemeState] = useState<ThemeMode>("dark");
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY) as ThemeMode | null;
      if (saved && ALL_THEME_CLASSES.includes(saved)) {
        setThemeState(saved);
        applyThemeToDocument(saved);
      } else {
        applyThemeToDocument("dark");
      }
    } catch {
      applyThemeToDocument("dark");
    }
    setIsReady(true);
  }, []);

  const setTheme = useCallback((newTheme: ThemeMode) => {
    const root = document.documentElement;
    root.classList.add("theme-transitioning");

    setThemeState(newTheme);
    try {
      localStorage.setItem(STORAGE_KEY, newTheme);
    } catch {
      // Ignore
    }

    applyThemeToDocument(newTheme);

    const timer = setTimeout(() => {
      root.classList.remove("theme-transitioning");
    }, 350);

    return () => clearTimeout(timer);
  }, []);

  const cycleTheme = useCallback(() => {
    const currentIndex = ALL_THEME_CLASSES.indexOf(theme);
    const nextIndex = (currentIndex + 1) % ALL_THEME_CLASSES.length;
    setTheme(ALL_THEME_CLASSES[nextIndex]);
  }, [theme, setTheme]);

  const toggleTheme = cycleTheme;

  return {
    theme,
    isDark: theme === "dark",
    isLight: theme === "light",
    isSteel: theme === "steel",
    isCircuit: theme === "circuit",
    isReady,
    setTheme,
    cycleTheme,
    toggleTheme,
    themes: THEME_OPTIONS,
  };
}
