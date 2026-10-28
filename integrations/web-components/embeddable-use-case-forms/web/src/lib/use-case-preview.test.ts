import { describe, expect, it } from 'vitest'

import type { CustomFieldDef, Question, Questionnaire } from '../types'
import {
  buildCustomFieldRows,
  buildPreviewSections,
  formatCustomFieldValue,
  parseCustomFieldNames,
  selectQuestionnaires,
  unmatchedQuestionnaireIds,
} from './use-case-preview'

function question(id: string, overrides: Partial<Question> = {}): Question {
  return {
    id,
    text: `Q ${id}`,
    evidence_type: 'short_text',
    select_options: [],
    multiple: false,
    required: false,
    ...overrides,
  }
}

function questionnaire(id: string, key: string | null = null): Questionnaire {
  return { id, key, name: id, sections: [] }
}

const ids = (qs: Questionnaire[]) => qs.map((q) => q.id)

describe('selectQuestionnaires', () => {
  const attached = [questionnaire('AGV1+1', 'AGV1'), questionnaire('TCV2+25', 'TCV2'), questionnaire('VEN+3')]

  it('keeps attachment order by default', () => {
    expect(ids(selectQuestionnaires(attached, [], []))).toEqual(['AGV1+1', 'TCV2+25', 'VEN+3'])
  })

  it('puts listed questionnaires first, in the listed order, matching on base id', () => {
    expect(ids(selectQuestionnaires(attached, [], ['VEN', 'TCV2+99']))).toEqual(['VEN+3', 'TCV2+25', 'AGV1+1'])
  })

  it('shows only allow-listed questionnaires, matching on base id', () => {
    expect(ids(selectQuestionnaires(attached, ['TCV2', 'VEN+3'], []))).toEqual(['TCV2+25', 'VEN+3'])
  })

  it('applies the allow list and the order together', () => {
    expect(ids(selectQuestionnaires(attached, ['AGV1', 'VEN'], ['VEN']))).toEqual(['VEN+3', 'AGV1+1'])
  })

  it('reports configured ids that match nothing attached', () => {
    expect(unmatchedQuestionnaireIds(attached, ['TCV2', 'GONE+1'])).toEqual(['GONE+1'])
  })
})

describe('buildPreviewSections', () => {
  const qn: Questionnaire = {
    id: 'qn-1',
    name: 'Intake',
    sections: [
      { id: 's-1', title: 'Basics', questions: [question('a'), question('b'), question('f', { evidence_type: 'file' })] },
      { id: 's-2', title: '', questions: [question('c')] },
      { id: 's-3', title: 'Empty', questions: [question('d')] },
    ],
  }

  it('keeps answered supported questions and drops empty sections', () => {
    const sections = buildPreviewSections([qn], { a: 'yes', b: '', f: 'x.pdf', c: ['EU'], d: [] })
    expect(sections).toEqual([
      { key: 'qn-1:s-1', title: 'Basics', rows: [{ id: 'a', label: 'Q a', value: 'yes' }] },
      { key: 'qn-1:s-2', title: 's-2', rows: [{ id: 'c', label: 'Q c', value: ['EU'] }] },
    ])
  })

  it('keeps a section id shared by two questionnaires apart', () => {
    const other: Questionnaire = { ...qn, id: 'qn-2', sections: [qn.sections[0]] }
    const keys = buildPreviewSections([qn, other], { a: 'yes' }).map((s) => s.key)
    expect(keys).toEqual(['qn-1:s-1', 'qn-2:s-1'])
  })
})

describe('parseCustomFieldNames', () => {
  it.each([undefined, null, '', '  '])('treats %j as unset (every field)', (raw) => {
    expect(parseCustomFieldNames(raw)).toEqual({ names: null, malformed: false })
  })

  it('parses a JSON array of names, including an empty one', () => {
    expect(parseCustomFieldNames('["Tier"]')).toEqual({ names: ['Tier'], malformed: false })
    expect(parseCustomFieldNames('[]')).toEqual({ names: [], malformed: false })
  })

  it.each(['Tier', '{"a":1}', '[oops'])('fails closed to none for %j', (raw) => {
    expect(parseCustomFieldNames(raw)).toEqual({ names: [], malformed: true })
  })
})

describe('formatCustomFieldValue', () => {
  it.each([
    [true, 'Yes'],
    [false, 'No'],
    [3, '3'],
    ['Gold', 'Gold'],
    ['  ', null],
    [null, null],
    [['EU', null, 'US'], ['EU', 'US']],
    [[], null],
    [{ a: 1 }, null],
  ])('formats %j as %j', (value, expected) => {
    expect(formatCustomFieldValue(value)).toEqual(expected)
  })
})

describe('buildCustomFieldRows', () => {
  const def = (id: string, name: string): CustomFieldDef => ({
    id,
    name,
    element_type: 'input',
    options: [],
    multiple: false,
  })
  const defs = [def('cf-1', 'Tier'), def('cf-2', 'Owner'), def('cf-3', 'Region')]
  const values = [
    { attributes: { custom_field_id: 'cf-1', value: 'Gold' } },
    { attributes: { custom_field_id: 'cf-2', value: 'Ops' } },
    { attributes: { custom_field_id: 'cf-3', value: '' } },
  ]

  it('shows every answered field, in tenant order, when unset', () => {
    expect(buildCustomFieldRows(defs, values, null).map((r) => r.label)).toEqual(['Tier', 'Owner'])
  })

  it('shows only the named fields, in the listed order', () => {
    expect(buildCustomFieldRows(defs, values, ['Owner', 'Missing', 'Tier']).map((r) => r.label)).toEqual([
      'Owner',
      'Tier',
    ])
  })

  it('shows none for an empty list', () => {
    expect(buildCustomFieldRows(defs, values, [])).toEqual([])
  })
})
