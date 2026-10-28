import type { CustomFieldDef, CustomFieldEntry } from '../types'

// element_type values confirmed against a real tenant's custom-field
// definitions: input, select, datepicker, checkbox. There is no confirmed
// "number" element_type anywhere — leave it unmapped (falls through to
// 'text') until checked against a tenant that actually has one.
export type CustomFieldControlType = 'select' | 'checkbox' | 'date' | 'text'

export function controlTypeForCustomField(def: CustomFieldDef): CustomFieldControlType {
  switch (def.element_type) {
    case 'select':
      return 'select'
    case 'checkbox':
      return 'checkbox'
    case 'datepicker':
      return 'date'
    default:
      return 'text'
  }
}

// Boolean-valued (checkbox) custom fields are excluded from the
// refresh-from-upstream diff entirely — the diff UI only renders text/select
// -shaped controls.
export function isBooleanCustomField(entry: CustomFieldEntry): boolean {
  return entry.def.element_type === 'checkbox'
}

export function customFieldFormKey(fieldId: string): string {
  return `cf:${fieldId}`
}

export function isCustomFieldFormKey(key: string): boolean {
  return key.startsWith('cf:')
}

export function customFieldIdFromFormKey(key: string): string {
  return key.slice('cf:'.length)
}

type Dict = Record<string, unknown>

function rec(v: unknown): Dict {
  return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Dict) : {}
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

// Parse a tenant's `GET /custom_fields` definitions document.
export function parseCustomFieldDefs(defs: unknown[]): CustomFieldDef[] {
  return defs.map((item) => {
    const attrs = rec(rec(item).attributes)
    return {
      id: String(rec(item).id),
      name: typeof attrs.name === 'string' ? attrs.name : '',
      element_type: typeof attrs.element_type === 'string' ? attrs.element_type : 'input',
      options: arr(attrs.options).map(String),
      multiple: Boolean(attrs.multiple),
    }
  })
}

// Resolve the tenant custom fields configured by name (customFieldNames)
// against the tenant's field catalog and this use case's current values.
// Names that don't match a real field are silently omitted — the
// "Additional information" section never renders as empty-but-present.
export function resolveConfiguredCustomFields(
  defs: CustomFieldDef[],
  values: unknown[],
  names: string[],
): CustomFieldEntry[] {
  const entries: CustomFieldEntry[] = []
  for (const name of names) {
    const def = defs.find((d) => d.name === name)
    if (!def) {
      continue
    }
    const valueItem = values.find((item) => {
      const attrs = rec(rec(item).attributes)
      let fieldId = attrs.custom_field_id
      if (fieldId === null || fieldId === undefined) {
        fieldId = rec(rec(rec(item).relationships).custom_field).data
        fieldId = rec(fieldId).id
      }
      return fieldId !== undefined && String(fieldId) === def.id
    })
    entries.push({
      def,
      value: valueItem ? (rec(rec(valueItem).attributes).value ?? null) : null,
    })
  }
  return entries
}
