/* Client-side check for IP allowlist entries, mirroring the server's
 * ipaddress.ip_network(entry, strict=False): an IPv4 or IPv6 address with an optional prefix
 * length. Host bits are allowed (203.0.113.7/24 means 203.0.113.0/24). The server stays the
 * authority; this only lets the form point at the bad line before anyone saves. */
export const MAX_ALLOWLIST = 200;

function ipv4(text: string): boolean {
  const parts = text.split(".");
  return (
    parts.length === 4 &&
    parts.every(
      (p) => /^\d{1,3}$/.test(p) && Number(p) <= 255 && !/^0\d/.test(p),
    )
  );
}

function ipv6(text: string): boolean {
  if (!text.includes(":") || !/^[0-9a-f:.]+$/i.test(text)) return false;
  try {
    // The URL parser implements the full IPv6 grammar (::, embedded IPv4, …).
    new URL(`http://[${text}]/`);
    return true;
  } catch {
    return false;
  }
}

export function isIpOrCidr(entry: string): boolean {
  const [address, prefix, extra] = entry.split("/");
  if (extra !== undefined || !address) return false;
  const v4 = ipv4(address);
  if (!v4 && !ipv6(address)) return false;
  if (prefix === undefined) return true;
  if (!/^\d{1,3}$/.test(prefix)) return false;
  return Number(prefix) <= (v4 ? 32 : 128);
}

export function parseAllowlist(text: string) {
  const entries = text
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter(Boolean);
  return {
    entries,
    invalid: entries.filter((e) => !isIpOrCidr(e)),
    tooMany: entries.length > MAX_ALLOWLIST,
  };
}
