import { describe, expect, it } from 'vitest'

import type { Questionnaire } from '../types'
import {
  collectBlockingRequiredQuestionIds,
  isBlankAnswer,
  isMalformedBlockingQuestionnaires,
  parseBlockingQuestionnaires,
  questionnaireBaseId,
  unmatchedBlockingIds,
} from './blocking-questionnaires'

function question(id: string, overrides: Partial<Questionnaire['sections'][number]['questions'][number]> = {}) {
  return {
    id,
    text: id,
    evidence_type: 'short_text',
    select_options: [],
    multiple: false,
    required: true,
    ...overrides,
  }
}

const TCV2: Questionnaire = {
  id: 'TCV2+25',
  key: 'TCV2',
  name: 'Transparency',
  sections: [
    {
      id: 'sec-1',
      title: 'One',
      questions: [
        question('q-required'),
        question('q-optional', { required: false }),
        question('q-file', { evidence_type: 'file' }),
      ],
    },
  ],
}

const OTHER: Questionnaire = {
  id: 'AUTO-INTK1+3',
  name: 'Intake',
  sections: [{ id: 'sec-2', title: 'Two', questions: [question('q-other')] }],
}

describe('questionnaireBaseId', () => {
  it('splits on + and keeps hyphens in the base id', () => {
    expect(questionnaireBaseId('AUTO-INTK1+3')).toBe('AUTO-INTK1')
    expect(questionnaireBaseId('TCV2')).toBe('TCV2')
  })
})

describe('isBlankAnswer', () => {
  it.each([undefined, null, '', '   ', []])('treats %j as blank', (value) => {
    expect(isBlankAnswer(value)).toBe(true)
  })
  it.each(['x', ['A'], 0, false])('treats %j as answered', (value) => {
    expect(isBlankAnswer(value)).toBe(false)
  })
})

describe('parseBlockingQuestionnaires', () => {
  it('normalizes version ids to base ids and skips junk entries', () => {
    expect([...parseBlockingQuestionnaires('["TCV2+25", " VRA ", "", 7, null]')]).toEqual(['TCV2', 'VRA'])
  })
  it.each([undefined, '', 'not json', '{"a":1}'])('degrades %j to nothing blocking', (raw) => {
    expect(parseBlockingQuestionnaires(raw).size).toBe(0)
  })
})

describe('isMalformedBlockingQuestionnaires', () => {
  it('flags non-array and unparseable values only', () => {
    expect(isMalformedBlockingQuestionnaires(undefined)).toBe(false)
    expect(isMalformedBlockingQuestionnaires('  ')).toBe(false)
    expect(isMalformedBlockingQuestionnaires('[]')).toBe(false)
    expect(isMalformedBlockingQuestionnaires('{"a":1}')).toBe(true)
    expect(isMalformedBlockingQuestionnaires('TCV2')).toBe(true)
  })
})

describe('collectBlockingRequiredQuestionIds', () => {
  it('returns required, supported questions of blocking questionnaires only', () => {
    const ids = collectBlockingRequiredQuestionIds([TCV2, OTHER], new Set(['TCV2']), new Set())
    expect([...ids]).toEqual(['q-required'])
  })
  it('matches on the questionnaire key, falling back to the id', () => {
    expect([...collectBlockingRequiredQuestionIds([OTHER], new Set(['AUTO-INTK1']), new Set())]).toEqual(['q-other'])
  })
  it('never blocks on a hidden question', () => {
    expect(collectBlockingRequiredQuestionIds([TCV2], new Set(['TCV2']), new Set(['q-required'])).size).toBe(0)
  })
  it('blocks nothing when unconfigured', () => {
    expect(collectBlockingRequiredQuestionIds([TCV2], new Set(), new Set()).size).toBe(0)
  })
})

describe('unmatchedBlockingIds', () => {
  it('lists configured ids that match no loaded questionnaire, sorted', () => {
    expect(unmatchedBlockingIds([TCV2], new Set(['ZED', 'TCV2', 'ABC']))).toEqual(['ABC', 'ZED'])
  })
})
