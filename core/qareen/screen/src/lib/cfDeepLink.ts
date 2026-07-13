// ---------------------------------------------------------------------------
// Cloudflare "Create Token" deep-link builder.
//
// Pre-fills the API-token template with the exact permission rows Qareen needs
// for Remote Access, so the operator just clicks "Continue to summary" →
// "Create Token" instead of hand-picking five permissions.
//
// Required scopes (Phase-1, minimal):
//   Account · Cloudflare Tunnel:Edit                          key 'argotunnel'
//   Account · Access: Apps and Policies:Edit                  key 'access'
//   Account · Access: Orgs, Identity Providers & Groups:Edit  key 'access_acct'
//             (needed because we may CREATE the onetimepin IdP)
//   Zone    · DNS:Edit                                        key 'dns'
//   Zone    · Zone:Read                                       key 'zone'
//
// The permissionGroupKeys query param is a JSON array of {key,type} that the
// dashboard reads to pre-select the rows. The key strings are documented as
// cosmetic / subject-to-change — if a row fails to pre-fill, the operator can
// still add it by hand. The :account placeholder lets the user pick the account.
// ---------------------------------------------------------------------------

export interface CfPermissionKey {
  key: string;
  type: 'edit' | 'read';
}

export const CF_PERMISSION_KEYS: CfPermissionKey[] = [
  { key: 'argotunnel', type: 'edit' },
  { key: 'dns', type: 'edit' },
  { key: 'zone', type: 'read' },
  { key: 'access', type: 'edit' },
  { key: 'access_acct', type: 'edit' },
];

const CF_TOKEN_NAME = 'Qareen Remote Access';

/**
 * Build the prefilled Cloudflare "Create Token" URL. Opening it lands the
 * operator on the token-creation screen with the five Qareen permission rows
 * already selected and the token name filled in.
 */
export function buildCfTokenDeepLink(): string {
  const name = encodeURIComponent(CF_TOKEN_NAME);
  const permissionGroupKeys = encodeURIComponent(JSON.stringify(CF_PERMISSION_KEYS));
  return (
    'https://dash.cloudflare.com/?to=/:account/api-tokens' +
    `&name=${name}` +
    `&permissionGroupKeys=${permissionGroupKeys}`
  );
}
