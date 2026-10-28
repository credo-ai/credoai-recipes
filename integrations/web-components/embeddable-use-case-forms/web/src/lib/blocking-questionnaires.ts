import type { Questionnaire } from '../types'
import { isSupported } from './render-rules'

// `blocking-questionnaires`: the questionnaires whose required questions block
// Save. Everywhere else the card only warns on an empty required question (it
// writes evidences, not questionnaire submissions), so this is opt-in per
// questionnaire. Ids are matched on the *base* id — `TCV2` matches `TCV2+25` —
// so a host config survives questionnaire version bumps.

// A questionnaire version id is `{baseId}+{version}`; the base id may itself
// contain hyphens, so always split on `+`.
export function questionnaireBaseId(id: string): string {
  return id.split('+')[0]
}

function baseIdOf(questionnaire: Questionnaire): string {
  return questionnaireBaseId(questionnaire.key || questionnaire.id)
}

// Stricter than render-rules' isEmptyValue: whitespace-only text counts as
// blank, so it can't satisfy a blocking required question.
export function isBlankAnswer(value: unknown): boolean {
  if (value === undefined || value === null) {
    return true
  }
  if (typeof value === 'string') {
    return value.trim() === ''
  }
  if (Array.isArray(value)) {
    return value.length === 0
  }
  return false
}

// Lenient: unset, malformed, or non-string entries degrade to "nothing blocks".
export function parseBlockingQuestionnaires(raw: string | null | undefined): Set<string> {
  const ids = new Set<string>()
  if (!raw) {
    return ids
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return ids
  }
  if (!Array.isArray(parsed)) {
    return ids
  }
  for (const entry of parsed) {
    if (typeof entry !== 'string' || entry.trim() === '') {
      continue
    }
    ids.add(questionnaireBaseId(entry.trim()))
  }
  return ids
}

// True when a non-empty value isn't a JSON array — worth a console warning,
// since the lenient parse above would otherwise fail silently.
export function isMalformedBlockingQuestionnaires(raw: string | null | undefined): boolean {
  if (!raw || raw.trim() === '') {
    return false
  }
  try {
    return !Array.isArray(JSON.parse(raw))
  } catch {
    return true
  }
}

// Required questions on blocking questionnaires, minus anything currently
// hidden (section-, question-, or questionnaire-level) — a hidden question
// never blocks, and neither does one the card can't render an editor for.
// Hidden-in-the-questionnaire questions are already dropped by
// compose.ts::parseQuestionnaire.
export function collectBlockingRequiredQuestionIds(
  questionnaires: Questionnaire[],
  blocking: Set<string>,
  hiddenQuestionIds: Set<string>,
): Set<string> {
  const ids = new Set<string>()
  if (blocking.size === 0) {
    return ids
  }
  for (const questionnaire of questionnaires) {
    if (!blocking.has(baseIdOf(questionnaire))) {
      continue
    }
    for (const section of questionnaire.sections) {
      for (const question of section.questions) {
        if (question.required && isSupported(question) && !hiddenQuestionIds.has(question.id)) {
          ids.add(question.id)
        }
      }
    }
  }
  return ids
}

// Configured base ids that match none of the loaded questionnaires, sorted.
export function unmatchedBlockingIds(questionnaires: Questionnaire[], blocking: Set<string>): string[] {
  if (blocking.size === 0) {
    return []
  }
  const loaded = new Set(questionnaires.map(baseIdOf))
  return [...blocking].filter((id) => !loaded.has(id)).sort()
}
