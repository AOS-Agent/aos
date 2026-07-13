import { useState, useCallback, useEffect } from 'react';
import { Moon as MoonIcon, Sun } from 'lucide-react';
import { SettingCard, SettingRow, Toggle } from './shared';
import type { SettingsSection } from './types';

// ---------------------------------------------------------------------------
// Appearance — theme & font size toggles (localStorage-persisted).
// ---------------------------------------------------------------------------

function AppearanceContent() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [fontSize, setFontSize] = useState<'default' | 'large'>('default');

  useEffect(() => {
    const saved = localStorage.getItem('qareen-theme') as 'dark' | 'light' | null;
    if (saved) setTheme(saved);
    const savedSize = localStorage.getItem('qareen-font-size') as 'default' | 'large' | null;
    if (savedSize) setFontSize(savedSize);
  }, []);

  const toggleTheme = useCallback((isDark: boolean) => {
    const next = isDark ? 'dark' : 'light';
    setTheme(next);
    localStorage.setItem('qareen-theme', next);
    document.documentElement.setAttribute('data-theme', next);
    document.documentElement.classList.toggle('dark', next === 'dark');
  }, []);

  const toggleFontSize = useCallback((isLarge: boolean) => {
    const next = isLarge ? 'large' : 'default';
    setFontSize(next);
    localStorage.setItem('qareen-font-size', next);
    document.documentElement.style.fontSize = isLarge ? '16px' : '';
  }, []);

  return (
    <SettingCard icon={theme === 'dark' ? MoonIcon : Sun} title="Appearance">
      <SettingRow
        label="Dark mode"
        description="Warm dark browns for the qareen's canvas"
        trailing={<Toggle checked={theme === 'dark'} onChange={toggleTheme} />}
      />
      <SettingRow
        label="Larger text"
        description="Increase base font size for readability"
        trailing={<Toggle checked={fontSize === 'large'} onChange={toggleFontSize} />}
      />
    </SettingCard>
  );
}

export const appearanceSection: SettingsSection = {
  id: 'appearance',
  title: 'Appearance',
  icon: MoonIcon,
  component: AppearanceContent,
};
