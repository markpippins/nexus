import { useEffect, useState, useCallback } from "react";

export type FontMode = "space-grotesk" | "system-ui";

export interface FontOption {
  id: FontMode;
  name: string;
  shortName: string;
  description: string;
  fontFamily: string;
}

export const FONT_OPTIONS: FontOption[] = [
  {
    id: "space-grotesk",
    name: "Space Grotesk",
    shortName: "Space Grotesk",
    description: "Default geometric display font with distinctive technical aesthetics",
    fontFamily: '"Space Grotesk", ui-sans-serif, system-ui, sans-serif',
  },
  {
    id: "system-ui",
    name: "System UI",
    shortName: "System UI",
    description: "Native OS system-ui font optimized for maximum legibility and accessibility",
    fontFamily:
      'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  },
];

const STORAGE_KEY = "relics_workbench_font";

function applyFontToDocument(font: FontMode) {
  const root = document.documentElement;
  if (font === "system-ui") {
    root.classList.add("font-system");
    root.classList.remove("font-space-grotesk");
    root.setAttribute("data-font", "system-ui");
  } else {
    root.classList.remove("font-system");
    root.classList.add("font-space-grotesk");
    root.setAttribute("data-font", "space-grotesk");
  }
}

export function useFont() {
  const [font, setFontState] = useState<FontMode>("space-grotesk");
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY) as FontMode | null;
      if (saved === "system-ui" || saved === "space-grotesk") {
        setFontState(saved);
        applyFontToDocument(saved);
      } else {
        applyFontToDocument("space-grotesk");
      }
    } catch {
      applyFontToDocument("space-grotesk");
    }
    setIsReady(true);
  }, []);

  const setFont = useCallback((newFont: FontMode) => {
    setFontState(newFont);
    try {
      localStorage.setItem(STORAGE_KEY, newFont);
    } catch {
      // Ignore in restricted environments
    }
    applyFontToDocument(newFont);
  }, []);

  const toggleFont = useCallback(() => {
    setFont(font === "space-grotesk" ? "system-ui" : "space-grotesk");
  }, [font, setFont]);

  return {
    font,
    isSystemFont: font === "system-ui",
    isSpaceGrotesk: font === "space-grotesk",
    isReady,
    setFont,
    toggleFont,
    fontOptions: FONT_OPTIONS,
  };
}
