import type { AnswerValue, CustomFieldDef, Questionnaire } from '../types'
import { questionnaireBaseId } from './blocking-questionnaires'
import { readCustomFieldValue } from './compose'
import { isEmptyValue, isSupported } from './render-rules'

// Pure logic behind <credo-ai-use-case-preview>: which questionnaires show and
// in what order, and which answers and custom fields become preview rows.
//
// Unlike the transparency card there is no saved layout and no section- or
// question-level control. The default order is the edit form's: questionnaires
// in the order they're attached to the use case, each one's sections in their
// own order, never merged across questionnaires.
//
// Questionnaire ids in `questionnaire-order` and `allowed-questionnaires` are
// matched on the base id (`TCV2` matches `TCV2+25`), as blocking-questionnaires
// does, so a host config survives questionnaire version bumps.

function baseIdOf(questionnaire: Questionnaire): string {
  return questionnaireBaseId(questionnaire.key || questionnaire.id)
}

function baseIds(ids: string[]): string[] {
  return ids.map((id) => questionnaireBaseId(id.trim()))
}

// `allowed-questionnaires`, then `questionnaire-order`. An empty allow list
// shows every attached questionnaire (the card's rule). Listed questionnaires
// come first, in the listed order; the rest keep their attachment order.
export function selectQuestionnaires(
  attached: Questionnaire[],
  allowedIds: string[],
  orderIds: string[],
): Questionnaire[] {
  const allowed = new Set(baseIds(allowedIds))
  const included =
    allowed.size === 0 ? attached : attached.filter((q) => allowed.has(baseIdOf(q)))

  const order = baseIds(orderIds)
  const rank = (q: Questionnaire) => {
    const index = order.indexOf(baseIdOf(q))
    return index === -1 ? order.length : index
  }
  // Array.prototype.sort is stable, so unlisted questionnaires keep their order.
  return [...included].sort((a, b) => rank(a) - rank(b))
}

// Configured ids that match no attached questionnaire, for a console warning.
export function unmatchedQuestionnaireIds(attached: Questionnaire[], ids: string[]): string[] {
  const present = new Set(attached.map(baseIdOf))
  return ids.filter((id) => !present.has(questionnaireBaseId(id.trim())))
}

export interface PreviewRowData {
  id: string
  label: string
  value: string | string[]
}

export interface PreviewSectionData {
  // questionnaire id + section id: a section id can repeat across questionnaires.
  key: string
  title: string
  rows: PreviewRowData[]
}

// One block per section with at least one answered, supported question — the
// card preview's rule. Unanswered questions and empty sections are omitted.
export function buildPreviewSections(
  questionnaires: Questionnaire[],
  answers: Record<string, AnswerValue>,
): PreviewSectionData[] {
  return questionnaires.flatMap((questionnaire) =>
    questionnaire.sections
      .map((section) => ({
        key: `${questionnaire.id}:${section.id}`,
        title: section.title || section.id,
        rows: section.questions
          .filter((q) => isSupported(q) && !isEmptyValue(answers[q.id]))
          .map((q) => ({ id: q.id, label: q.text, value: answers[q.id] as string | string[] })),
      }))
      .filter((section) => section.rows.length > 0),
  )
}

// `custom-fields`: unset or blank shows every custom field, in the tenant's
// order. A JSON array of field names shows those, in the listed order; `[]`
// shows none. Anything else fails closed to none, flagged so the caller warns.
export function parseCustomFieldNames(raw: string | null | undefined): {
  names: string[] | null
  malformed: boolean
} {
  if (!raw || raw.trim() === '') {
    return { names: null, malformed: false }
  }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      return { names: parsed.filter((v): v is string => typeof v === 'string'), malformed: false }
    }
  } catch {
    // fall through
  }
  return { names: [], malformed: true }
}

// A custom-field value as preview text, or null when there's nothing to show.
// Checkbox fields are booleans; objects have no display form and are skipped.
export function formatCustomFieldValue(value: unknown): string | string[] | null {
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  if (typeof value === 'number') {
    return String(value)
  }
  if (typeof value === 'string') {
    return value.trim() === '' ? null : value
  }
  if (Array.isArray(value)) {
    const items = value
      .filter((v) => v !== null && v !== undefined && typeof v !== 'object')
      .map(String)
    return items.length > 0 ? items : null
  }
  return null
}

export function buildCustomFieldRows(
  defs: CustomFieldDef[],
  values: unknown[],
  names: string[] | null,
): PreviewRowData[] {
  const selected =
    names === null
      ? defs
      : names.flatMap((name) => defs.filter((def) => def.name === name).slice(0, 1))
  return selected.flatMap((def) => {
    const value = formatCustomFieldValue(readCustomFieldValue(values, def.id))
    return value === null ? [] : [{ id: def.id, label: def.name, value }]
  })
}
