// Lenient JSON-array-of-strings parsing for env-var-sourced config. Malformed
// or absent input degrades to an empty list rather than throwing — this is
// optional, cosmetic configuration, not something that should crash the app.
export function parseJsonStringArray(raw: string | null | undefined): string[] {
  if (!raw) {
    return []
  }
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : []
  } catch {
    return []
  }
}
