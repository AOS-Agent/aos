import { create } from "zustand";

export interface UIState {
  sidebarOpen: boolean;
  commandPaletteOpen: boolean;
  activeScreen: string;

  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  toggleCommandPalette: () => void;
  setCommandPaletteOpen: (open: boolean) => void;
  setActiveScreen: (screen: string) => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  commandPaletteOpen: false,
  activeScreen: "home",

  toggleSidebar: () =>
    set((state) => ({ sidebarOpen: !state.sidebarOpen })),

  setSidebarOpen: (open) =>
    set({ sidebarOpen: open }),

  toggleCommandPalette: () =>
    set((state) => ({ commandPaletteOpen: !state.commandPaletteOpen })),

  setCommandPaletteOpen: (open) =>
    set({ commandPaletteOpen: open }),

  setActiveScreen: (screen) =>
    set({ activeScreen: screen }),
}));
