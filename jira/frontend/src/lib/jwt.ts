// Minimal, dependency-free JWT payload decoder.
// Base64url-decodes the middle segment of a JWT. Never throws — returns null
// on any malformed / missing input. We only read claims (e.g. `imp`, `act`);
// signature verification is the server's job.
export function decodeJwt(token: string | null | undefined): Record<string, unknown> | null {
  if (!token) return null;
  try {
    const parts = token.split('.');
    if (parts.length < 2) return null;
    const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    // Pad to a multiple of 4 for atob.
    const padded = payload + '='.repeat((4 - (payload.length % 4)) % 4);
    const json = atob(padded);
    // Handle UTF-8 payloads correctly.
    const decoded = decodeURIComponent(
      Array.prototype.map
        .call(json, (c: string) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    const parsed = JSON.parse(decoded);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
