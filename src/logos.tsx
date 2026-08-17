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

/** Perceived luminance — dark brand marks get rendered white on our dark UI. */
function isDark(hex: string): boolean {
  const n = parseInt(hex, 16);
  const r = (n >> 16) & 255,
    g = (n >> 8) & 255,
    b = n & 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 90;
}

export function ConnectorLogo({ id, name, size = 34 }: { id: string; name: string; size?: number }) {
  const icon = ICONS[id];
  return (
    <div
      style={{ width: size, height: size }}
      className="rounded-[10px] border border-zinc-800 bg-zinc-900 flex items-center justify-center shrink-0"
    >
      {icon ? (
        <svg
          role="img"
          viewBox="0 0 24 24"
          width={size * 0.55}
          height={size * 0.55}
          fill={isDark(icon.hex) ? "#e4e4e7" : `#${icon.hex}`}
        >
          <path d={icon.path} />
        </svg>
      ) : (
        <span className="text-[13px] font-semibold text-zinc-200">{name.charAt(0)}</span>
      )}
    </div>
  );
}
