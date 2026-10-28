import { describe, expect, it } from 'vitest'

import type { Question } from '../types'
import {
  controlTypeFor,
  isEmptyValue,
  isSupported,
  matchesStatusTrigger,
  validationRulesFor,
} from './render-rules'

function question(overrides: Partial<Question>): Question {
  return {
    id: 'q-1',
    text: 'Question?',
    evidence_type: 'short_text',
    select_options: [],
    multiple: false,
    required: false,
    ...overrides,
  }
}

const FEW_OPTIONS = ['a', 'b', 'c']
const MANY_OPTIONS = ['a', 'b', 'c', 'd', 'e', 'f', 'g']

describe('controlTypeFor', () => {
  it('maps scalar evidence types', () => {
    expect(controlTypeFor(question({ evidence_type: 'text' }))).toBe('textarea')
    expect(controlTypeFor(question({ evidence_type: 'short_text' }))).toBe('input')
    expect(controlTypeFor(question({ evidence_type: 'number' }))).toBe('number')
    expect(controlTypeFor(question({ evidence_type: 'date' }))).toBe('date')
  })

  it('maps selects by multiplicity and option count', () => {
    expect(
      controlTypeFor(question({ evidence_type: 'select', select_options: FEW_OPTIONS })),
    ).toBe('radio')
    expect(
      controlTypeFor(question({ evidence_type: 'select', select_options: MANY_OPTIONS })),
    ).toBe('select')
    expect(
      controlTypeFor(
        question({ evidence_type: 'select', select_options: FEW_OPTIONS, multiple: true }),
      ),
    ).toBe('checkbox-group')
    expect(
      controlTypeFor(
        question({ evidence_type: 'select', select_options: MANY_OPTIONS, multiple: true }),
      ),
    ).toBe('chip-select')
  })

  it('marks file and user questions unsupported', () => {
    expect(controlTypeFor(question({ evidence_type: 'file' }))).toBe('unsupported')
    expect(controlTypeFor(question({ evidence_type: 'user' }))).toBe('unsupported')
    expect(isSupported(question({ evidence_type: 'file' }))).toBe(false)
  })

  it('falls back to input for unknown evidence types', () => {
    expect(controlTypeFor(question({ evidence_type: 'something-new' }))).toBe('input')
  })
})

describe('validationRulesFor', () => {
  it('caps text inputs at 1000 characters', () => {
    const rules = validationRulesFor(question({ evidence_type: 'text' }))
    expect(rules.maxLength?.value).toBe(1000)
  })

  it('caps multi-selects at 10 selections', () => {
    const rules = validationRulesFor(
      question({ evidence_type: 'select', select_options: FEW_OPTIONS, multiple: true }),
    )
    expect(rules.validate?.(['a', 'b'])).toBe(true)
    expect(rules.validate?.(Array.from({ length: 11 }, (_, i) => `v${i}`))).toBe(
      'Maximum 10 selections',
    )
  })

  it('leaves radios and dates unconstrained', () => {
    expect(
      validationRulesFor(question({ evidence_type: 'select', select_options: FEW_OPTIONS })),
    ).toEqual({})
    expect(validationRulesFor(question({ evidence_type: 'date' }))).toEqual({})
  })
})

describe('matchesStatusTrigger', () => {
  it('matches a scalar value against the trigger list', () => {
    expect(matchesStatusTrigger('Beta', ['Beta', 'GA'])).toBe(true)
    expect(matchesStatusTrigger('Alpha', ['Beta', 'GA'])).toBe(false)
  })

  it('matches if any element of an array value is in the trigger list', () => {
    expect(matchesStatusTrigger(['Internal', 'Beta'], ['Beta', 'GA'])).toBe(true)
    expect(matchesStatusTrigger(['Internal'], ['Beta', 'GA'])).toBe(false)
  })

  it('never matches when unconfigured or the value is null', () => {
    expect(matchesStatusTrigger('Beta', [])).toBe(false)
    expect(matchesStatusTrigger(null, ['Beta'])).toBe(false)
  })
})

describe('isEmptyValue', () => {
  it('treats undefined, null, empty string, and empty array as empty', () => {
    expect(isEmptyValue(undefined)).toBe(true)
    expect(isEmptyValue(null)).toBe(true)
    expect(isEmptyValue('')).toBe(true)
    expect(isEmptyValue([])).toBe(true)
    expect(isEmptyValue('x')).toBe(false)
    expect(isEmptyValue(['x'])).toBe(false)
  })
})
