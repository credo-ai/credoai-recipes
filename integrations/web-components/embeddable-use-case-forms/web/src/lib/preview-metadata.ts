import type { MetadataRow } from '../types'

function formatLabel(propertyName: string): string {
  return propertyName
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(' ')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

// A lighter-weight version of the snake_case/camelCase dedup problem a UI
// with two casing conventions would need — this codebase only ever has one
// (snake_case API wire names), so this just catches accidental case-variant
// duplicates in the configured name list (e.g. "Name" and "name").
function normalizePropertyName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '')
}

// snake_case -> camelCase, so a configured "risk_classification_level" also
// resolves against an attributes bag keyed "riskClassificationLevel".
function toCamelCase(name: string): string {
  return name.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase())
}

// Build Preview metadata Label/Value rows from useCaseFields.
// - A name with no matching property warns and is skipped.
// - A property that exists but is null/blank renders as an em-dash
//   placeholder — no warning, that's expected for an unset field.
// - A list/object-valued property isn't displayable — silently skipped.
// - Duplicate requests (by normalized name) collapse to one row.
export function buildMetadataRows(
  attributes: Record<string, unknown>,
  requestedNames: string[],
  warn: (message: string) => void = (m) => console.warn(m),
): MetadataRow[] {
  const rows: MetadataRow[] = []
  const seen = new Set<string>()
  for (const name of requestedNames) {
    const normalized = normalizePropertyName(name)
    if (seen.has(normalized)) {
      continue
    }
    const resolvedKey = name in attributes ? name : toCamelCase(name)
    if (!(resolvedKey in attributes)) {
      warn(`Preview metadata: use-case property "${name}" does not exist`)
      continue
    }
    const value = attributes[resolvedKey]
    if (Array.isArray(value) || (typeof value === 'object' && value !== null)) {
      continue
    }
    seen.add(normalized)
    const isBlank = value === null || value === undefined || String(value).trim() === ''
    rows.push({ key: name, label: formatLabel(name), value: isBlank ? null : String(value) })
  }
  return rows
}
