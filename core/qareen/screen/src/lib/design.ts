// ---------------------------------------------------------------------------
// AOS Design System — TypeScript token constants (neutral-warm charcoal)
// Mirrors globals.css @theme block. Use these when programmatic access to
// tokens is needed (e.g. charts, canvas, conditional styling). For CSS/Tailwind
// use the custom properties defined in globals.css.
// ---------------------------------------------------------------------------

export const colors = {
  bg: "#0B0B0A",
  bgPanel: "#121210",
  bgSecondary: "#1A1917",
  bgTertiary: "#24221F",
  bgQuaternary: "#302E2A",

  text: "#FFFFFF",
  textSecondary: "#E7E4DE",
  textTertiary: "#97938C",
  textQuaternary: "#66625B",

  border: "rgba(245, 242, 236, 0.07)",
  borderSecondary: "rgba(245, 242, 236, 0.11)",
  borderTertiary: "rgba(245, 242, 236, 0.16)",

  accent: "#D6CCB4",
  accentHover: "#E4DCC8",
  accentMuted: "#1B1915",
  accentSubtle: "rgba(214, 204, 180, 0.14)",
  onAccent: "#14130E",

  hover: "rgba(245, 242, 236, 0.05)",
  active: "rgba(245, 242, 236, 0.08)",
  selected: "rgba(245, 242, 236, 0.12)",

  green: "#30D158",
  greenMuted: "#0D1F12",
  yellow: "#FFD60A",
  yellowMuted: "#1C1A08",
  red: "#FF453A",
  redMuted: "#1F0F0E",
  blue: "#0A84FF",
  blueMuted: "#0A1520",
} as const;

export const typography = {
  title: { size: "22px", weight: "680", tracking: "-0.025em", lineHeight: "1.15" },
  heading: { size: "15px", weight: "600", tracking: "-0.01em", lineHeight: "1.35" },
  body: { size: "13px", weight: "400", tracking: "-0.008em", lineHeight: "1.5" },
  label: { size: "13px", weight: "510", tracking: "-0.008em", lineHeight: "1.4" },
  caption: { size: "11px", weight: "400", tracking: "0em", lineHeight: "1.45" },
  overline: { size: "10px", weight: "590", tracking: "0.06em", lineHeight: "1.2", transform: "uppercase" },
  tiny: { size: "10px", weight: "510", tracking: "0.04em", lineHeight: "1.2" },
} as const;

export const spacing = {
  sectionGap: "32px",
  cardPadding: "16px",
  rowHeight: "36px",
  sidebarItemHeight: "28px",
  sidebarWidth: "200px",
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
