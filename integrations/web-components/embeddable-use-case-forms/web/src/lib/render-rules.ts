import type { AnswerValue, Question } from '../types'

// Everything about how a question renders derives from its API metadata
// (evidence_type + multiple + select_options) — never from a hardcoded
// question id. Mirrors the platform's getQuestionDisplayProps mapping.

export type ControlType =
  | 'textarea'
  | 'input'
  | 'number'
  | 'date'
  | 'radio'
  | 'select'
  | 'checkbox-group'
  | 'chip-select'
  | 'unsupported'

export const TEXT_MAX_LENGTH = 1000
export const MULTI_SELECT_MAX = 10
// Below this option count, selects render as radio/checkbox groups.
const INLINE_OPTIONS_THRESHOLD = 6

export function controlTypeFor(q: Question): ControlType {
  switch (q.evidence_type) {
    case 'text':
      return 'textarea'
    case 'short_text':
      return 'input'
    case 'number':
      return 'number'
    case 'date':
      return 'date'
    case 'select': {
      const inline = q.select_options.length < INLINE_OPTIONS_THRESHOLD
      if (q.multiple) {
        return inline ? 'checkbox-group' : 'chip-select'
      }
      return inline ? 'radio' : 'select'
    }
    case 'file':
    case 'user':
      return 'unsupported'
    default:
      return 'input'
  }
}

export function isSupported(q: Question): boolean {
  return controlTypeFor(q) !== 'unsupported'
}

// react-hook-form register options for a question.
export function validationRulesFor(q: Question): {
  maxLength?: { value: number; message: string }
  validate?: (value: unknown) => true | string
} {
  const control = controlTypeFor(q)
  if (control === 'textarea' || control === 'input') {
    return {
      maxLength: {
        value: TEXT_MAX_LENGTH,
        message: `Maximum ${TEXT_MAX_LENGTH} characters`,
      },
    }
  }
  if (control === 'checkbox-group' || control === 'chip-select') {
    return {
      validate: (value: unknown) =>
        !Array.isArray(value) || value.length <= MULTI_SELECT_MAX
          ? true
          : `Maximum ${MULTI_SELECT_MAX} selections`,
    }
  }
  return {}
}

// Drives the config-driven status badge (statusQuestionId +
// statusQuestionOptions): does a question's current answer match one
// of the configured trigger values? Not derived from any platform metadata
// signal — purely a function of host configuration, same as statusFieldName.
export function matchesStatusTrigger(value: AnswerValue, triggerValues: string[]): boolean {
  if (triggerValues.length === 0 || value === null) {
    return false
  }
  return Array.isArray(value)
    ? value.some((v) => triggerValues.includes(v))
    : triggerValues.includes(value)
}

export function isEmptyValue(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === '' ||
    (Array.isArray(value) && value.length === 0)
  )
}
