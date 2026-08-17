import { useState } from "react";
import {
  siGoogle,
  siGithub,
  siTelegram,
  siWhatsapp,
  siClickup,
  siObsidian,
  siElevenlabs,
  siOpenrouter,
  siPaypal,
  siCloudflare,
  siNotion,
  siLinear,
  siDiscord,
  siTodoist,
} from "simple-icons";

interface BrandIcon {
  path: string;
  hex: string;
}

const ICONS: Record<string, BrandIcon> = {
  google: siGoogle,
  github: siGithub,
  telegram: siTelegram,
  whatsapp: siWhatsapp,
  clickup: siClickup,
  obsidian: siObsidian,
  elevenlabs: siElevenlabs,
  openrouter: siOpenrouter,
  paypal: siPaypal,
  cloudflare: siCloudflare,
  notion: siNotion,
  linear: siLinear,
  discord: siDiscord,
  todoist: siTodoist,
};

/**
 * Curated id → domain map. Feeds tier two (favicon service) so catalog and
 * browse entries we have no bundled brand mark for still render a real logo
 * instead of a bare letter.
 */
export const DOMAINS: Record<string, string> = {
  google: "google.com",
  github: "github.com",
  telegram: "telegram.org",
  whatsapp: "whatsapp.com",
  slack: "slack.com",
  clickup: "clickup.com",
  obsidian: "obsidian.md",
  elevenlabs: "elevenlabs.io",
  openrouter: "openrouter.ai",
  paypal: "paypal.com",
  cloudflare: "cloudflare.com",
  notion: "notion.so",
  linear: "linear.app",
  discord: "discord.com",
  todoist: "todoist.com",
  composio: "composio.dev",
  gmail: "gmail.com",
  googlecalendar: "calendar.google.com",
  googledrive: "drive.google.com",
  x: "x.com",
  reddit: "reddit.com",
  jira: "atlassian.com",
  asana: "asana.com",
  trello: "trello.com",
  dropbox: "dropbox.com",
  airtable: "airtable.com",
  figma: "figma.com",
  stripe: "stripe.com",
  shopify: "shopify.com",
  hubspot: "hubspot.com",
  wave: "waveapps.com",
  chitchats: "chitchats.com",
  plane: "plane.so",
};

/** The domain we would use for a connector id, if we know one. */
export function domainFor(id: string): string | undefined {
  return DOMAINS[id.toLowerCase()];
}

export function faviconUrl(domain: string, size = 64): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=${size}`;
}

/** Perceived luminance — dark brand marks get rendered white on our dark UI. */
function isDark(hex: string): boolean {
  const n = parseInt(hex, 16);
  const r = (n >> 16) & 255,
    g = (n >> 8) & 255,
    b = n & 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 90;
}

export function ConnectorLogo({
  id,
  name,
  size = 34,
  domain,
}: {
  id: string;
  name: string;
  size?: number;
  domain?: string | null;
}) {
  const icon = ICONS[id];
  const host = domain ?? domainFor(id);
  const [faviconFailed, setFaviconFailed] = useState(false);
  const radius = Math.round(size * 0.29);

  if (icon) {
    return (
      <div
        style={{ width: size, height: size, borderRadius: radius }}
        className="border border-zinc-800 bg-zinc-900 flex items-center justify-center shrink-0"
      >
        <svg
          role="img"
          viewBox="0 0 24 24"
          width={size * 0.55}
          height={size * 0.55}
          fill={isDark(icon.hex) ? "#e4e4e7" : `#${icon.hex}`}
        >
          <path d={icon.path} />
        </svg>
      </div>
    );
  }

  if (host && !faviconFailed) {
    return (
      <img
        src={faviconUrl(host, size <= 34 ? 64 : 128)}
        alt=""
        style={{ width: size, height: size, borderRadius: radius, padding: Math.max(3, size * 0.12) }}
        className="border border-zinc-800 bg-white/95 object-contain shrink-0"
        onError={() => setFaviconFailed(true)}
      />
    );
  }

  return (
    <div
      style={{ width: size, height: size, borderRadius: radius }}
      className="border border-zinc-800 bg-zinc-900 flex items-center justify-center shrink-0"
    >
      <span style={{ fontSize: Math.max(11, size * 0.38) }} className="font-semibold text-zinc-200">
        {name.charAt(0).toUpperCase()}
      </span>
    </div>
  );
}
