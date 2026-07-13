// ---------------------------------------------------------------------------
// Settings section registry — each pill in the left nav = one page.
// To add a section: create a file, export a SettingsSection object, add here.
// ---------------------------------------------------------------------------

export type { SettingsSection } from './types';
export { SettingCard, SettingRow, Toggle, LoadingRows } from './shared';
export { SectionNav } from './SectionNav';

import { generalSection } from './GeneralSection';
import { notificationsSection } from './NotificationsSection';

import type { SettingsSection } from './types';

export const SETTINGS_SECTIONS: SettingsSection[] = [
  generalSection,
  notificationsSection,
];
