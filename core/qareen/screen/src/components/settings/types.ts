import type { LucideIcon } from 'lucide-react';
import type { ComponentType } from 'react';

/** Metadata for a single settings section. */
export interface SettingsSection {
  /** URL-safe identifier — used in ?section= and scroll targets. */
  id: string;
  /** Display title in section header and nav pill. */
  title: string;
  /** Lucide icon for the section header. */
  icon: LucideIcon;
  /** The component that renders this section's content rows. */
  component: ComponentType;
}
