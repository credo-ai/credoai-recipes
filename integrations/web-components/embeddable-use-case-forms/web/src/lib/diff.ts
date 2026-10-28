import type { AnswerValue } from '../types'

// Port of the web component's detect-overrides diff, keyed by question id
// instead of a hardcoded card-field name.

export interface DiffState {
  oldValue: string | string[]
  newValue: string | string[]
}

export type DiffMap = Map<string, DiffState>

// Compares upstream answers against the live form values. Iterates upstream
// keys only: a question the user filled locally but that has no upstream
// evidence is not a diff. Empty upstream values are skipped for the same
// reason.
export function detectChanges(
  currentFormValues: Record<string, unknown>,
  freshAnswers: Record<string, AnswerValue>,
): DiffMap {
  const result: DiffMap = new Map()
  for (const [questionId, fresh] of Object.entries(freshAnswers)) {
    if (fresh === null || fresh === '' || (Array.isArray(fresh) && fresh.length === 0)) {
      continue
    }
    const saved = currentFormValues[questionId]
    if (!valuesEqual(saved, fresh)) {
      result.set(questionId, {
        oldValue: toDisplayValue(saved),
        newValue: fresh,
      })
    }
  }
  return result
}

// Order-insensitive: non-string elements in saved are filtered out before compare.
function valuesEqual(saved: unknown, fresh: string | string[]): boolean {
  if (Array.isArray(fresh)) {
    if (!Array.isArray(saved)) {
      return false
    }
    const filteredSaved = saved
      .filter((x): x is string => typeof x === 'string')
      .sort((a, b) => a.localeCompare(b))
    if (filteredSaved.length !== fresh.length) {
      return false
    }
    const sortedFresh = [...fresh].sort((a, b) => a.localeCompare(b))
    return filteredSaved.every((v, i) => v === sortedFresh[i])
  }
  // non-string saved (number, boolean, object) never === a string fresh.
  return saved === fresh
}

// undefined/null/non-string → '' so the diff UI always gets a renderable value.
function toDisplayValue(v: unknown): string | string[] {
  if (Array.isArray(v)) {
    return v.filter((x): x is string => typeof x === 'string')
  }
  return typeof v === 'string' ? v : ''
}
