// ---------------------------------------------------------------------------
// AOS Design System — TypeScript token constants (warm dark mode)
// Mirrors globals.css @theme block. Use these when programmatic access to
// tokens is needed (e.g. charts, canvas, conditional styling). For CSS/Tailwind
// use the custom properties defined in globals.css.
// ---------------------------------------------------------------------------

export const colors = {
  // Backgrounds — warm dark browns
  bg: "#161210",
  bgPanel: "#1c1814",
  bgSecondary: "#211d19",
  bgTertiary: "#28231e",
  bgQuaternary: "#302a24",

  // Text — white primary, warm gray hierarchy
  text: "#FFFFFF",
  textSecondary: "#E0DBD5",
  textTertiary: "#9A9590",
  textQuaternary: "#6E6862",

  // Borders — warm white opacity
  border: "rgba(255, 230, 210, 0.06)",
  borderSecondary: "rgba(255, 230, 210, 0.10)",
  borderTertiary: "rgba(255, 230, 210, 0.15)",

  // Accent (AOS orange)
  accent: "#D9730D",
  accentHover: "#E8842A",
  accentMuted: "#2C1A0E",
  accentSubtle: "rgba(217, 115, 13, 0.15)",

  // Overlays — warm white alpha
  hover: "rgba(255, 230, 210, 0.05)",
  active: "rgba(255, 230, 210, 0.09)",
  selected: "rgba(255, 230, 210, 0.13)",

  // Status colors — warm-shifted
  green: "#3DAD6A",
  greenMuted: "#141C16",
  yellow: "#D4A017",
  yellowMuted: "#1C1808",
  red: "#C9534A",
  redMuted: "#1C100F",
  blue: "#5A9FC4",
  blueMuted: "#101820",
} as const;

export const typography = {
  pageTitle: { size: "22px", weight: "680", tracking: "-0.025em", lineHeight: "1.15" },
  sectionHeader: { size: "10px", weight: "590", tracking: "0.06em", lineHeight: "1.2", transform: "uppercase" },
  body: { size: "13px", weight: "400", tracking: "-0.008em", lineHeight: "1.5" },
  rowLabel: { size: "13px", weight: "510", tracking: "-0.008em", lineHeight: "1.4" },
  small: { size: "11px", weight: "400", tracking: "0em", lineHeight: "1.45" },
  metadata: { size: "11px", weight: "400", tracking: "0.02em", lineHeight: "1.4" },
  tiny: { size: "10px", weight: "510", tracking: "0.04em", lineHeight: "1.2" },
} as const;

export const spacing = {
  sectionGap: "32px",
  cardPadding: "16px",
  rowHeight: "36px",
  sidebarItemHeight: "28px",
  sidebarWidth: "224px",
  sidebarCollapsed: "52px",
  topbarHeight: "48px",
  contentPadding: "24px",
} as const;

export const radius = {
  xs: "3px",
  sm: "5px",
  default: "7px",
  lg: "10px",
  xl: "14px",
  full: "9999px",
} as const;

export const transitions = {
  instant: "80ms",
  fast: "150ms",
  normal: "220ms",
  slow: "350ms",
  easeOut: "cubic-bezier(0.25, 0.46, 0.45, 0.94)",
  easeOutBack: "cubic-bezier(0.34, 1.56, 0.64, 1)",
  easeInOut: "cubic-bezier(0.4, 0, 0.2, 1)",
} as const;
