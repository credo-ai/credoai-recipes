import { describe, expect, it } from 'vitest'

import type { QuestionnairePreview } from '../api/platform'
import type { Question, Questionnaire, Section } from '../types'
import {
  buildFormDefaults,
  customFieldName,
  isQuestionVisible,
  missingRequiredQuestions,
  parseUseCaseForm,
  questionFieldName,
  supportedQuestions,
  toPreviewAnswers,
  visibleSections,
} from './use-case-form'

function question(overrides: Partial<Question> & { id: string }): Question {
  return {
    text: `Question ${overrides.id}`,
    evidence_type: 'text',
    select_options: [],
    multiple: false,
    required: false,
    ...overrides,
  }
}

function section(id: string, questions: Question[]): Section {
  return { id, title: `Section ${id}`, questions }
}

function questionnaire(id: string, sections: Section[]): Questionnaire {
  return { id, name: `Questionnaire ${id}`, sections }
}

describe('field names', () => {
  it('builds the nested question path the integration payload is keyed on', () => {
    expect(questionFieldName('qn1', 's1', 'q1')).toBe('questions.qn1.s1.q1')
    expect(customFieldName('cf1')).toBe('customFields.cf1')
  })
})

describe('supportedQuestions', () => {
  it('drops file and user questions, which have no control', () => {
    const s = section('s1', [
      question({ id: 'text' }),
      question({ id: 'file', evidence_type: 'file' }),
      question({ id: 'user', evidence_type: 'user' }),
      question({ id: 'select', evidence_type: 'select', select_options: ['a'] }),
    ])
    expect(supportedQuestions(s).map((q) => q.id)).toEqual(['text', 'select'])
  })
})

describe('parseUseCaseForm', () => {
  it('flattens questions and custom fields into the submission payload', () => {
    const result = parseUseCaseForm({
      name: 'Fraud triage',
      description: 'Scores transactions',
      customFields: { cf1: 'Ops', cf2: '' },
      questions: {
        qn1: { s1: { q1: 'yes', q2: '' }, s2: { q3: ['a', 'b'] } },
      },
    })
    expect(result.name).toBe('Fraud triage')
    expect(result.customFields).toEqual([{ customFieldId: 'cf1', value: 'Ops' }])
    expect(result.questions).toEqual([
      { questionnaireId: 'qn1', sectionId: 's1', questionId: 'q1', value: 'yes' },
      { questionnaireId: 'qn1', sectionId: 's2', questionId: 'q3', value: ['a', 'b'] },
    ])
  })

  it('keeps an emptied multi-select as [] so the upsert clears it upstream', () => {
    const result = parseUseCaseForm({ questions: { qn1: { s1: { q1: [] } } } })
    expect(result.questions).toEqual([
      { questionnaireId: 'qn1', sectionId: 's1', questionId: 'q1', value: [] },
    ])
  })

  it('tolerates entirely absent sections', () => {
    expect(parseUseCaseForm({}).questions).toEqual([])
    expect(parseUseCaseForm({}).customFields).toEqual([])
  })
})

describe('toPreviewAnswers', () => {
  it('flattens one questionnaire and ignores the others', () => {
    const answers = toPreviewAnswers(
      { questions: { qn1: { s1: { q1: 'a' } }, qn2: { s9: { q9: 'z' } } } },
      'qn1',
    )
    expect(answers).toEqual([{ section_id: 's1', question_id: 'q1', value: 'a' }])
  })

  it('returns nothing for a questionnaire with no answers yet', () => {
    expect(toPreviewAnswers({ questions: {} }, 'qn1')).toEqual([])
  })
})

describe('preview visibility', () => {
  const preview: QuestionnairePreview = {
    sections: [
      {
        id: 's1',
        questions: [
          { id: 'q1', visible: true },
          { id: 'q2', visible: false },
        ],
      },
    ],
  }

  it('hides only questions explicitly marked not visible', () => {
    expect(isQuestionVisible(preview, 's1', question({ id: 'q1' }))).toBe(true)
    expect(isQuestionVisible(preview, 's1', question({ id: 'q2' }))).toBe(false)
  })

  it('treats an unmentioned question or section as visible', () => {
    expect(isQuestionVisible(preview, 's1', question({ id: 'q3' }))).toBe(true)
    expect(isQuestionVisible(preview, 's2', question({ id: 'q1' }))).toBe(true)
  })

  it('treats a missing preview as everything visible', () => {
    expect(isQuestionVisible(undefined, 's1', question({ id: 'q2' }))).toBe(true)
  })

  it('shows a conditional (hidden) question only once the preview reveals it', () => {
    expect(isQuestionVisible(preview, 's1', question({ id: 'q1', hidden: true }))).toBe(true)
    expect(isQuestionVisible(preview, 's1', question({ id: 'q2', hidden: true }))).toBe(false)
    expect(isQuestionVisible(preview, 's1', question({ id: 'q3', hidden: true }))).toBe(false)
    expect(isQuestionVisible(undefined, 's1', question({ id: 'q1', hidden: true }))).toBe(false)
  })

  it('drops a section whose every supported question is hidden', () => {
    const qn = questionnaire('qn1', [
      section('s1', [question({ id: 'q1' }), question({ id: 'q2' })]),
      section('s2', [question({ id: 'q3' })]),
    ])
    const allHidden: QuestionnairePreview = {
      sections: [
        {
          id: 's1',
          questions: [
            { id: 'q1', visible: false },
            { id: 'q2', visible: false },
          ],
        },
      ],
    }
    expect(visibleSections(qn, allHidden).map((s) => s.id)).toEqual(['s2'])
  })

  it('drops a section that only holds unsupported questions', () => {
    const qn = questionnaire('qn1', [
      section('s1', [question({ id: 'f', evidence_type: 'file' })]),
      section('s2', [question({ id: 'q1' })]),
    ])
    expect(visibleSections(qn, undefined).map((s) => s.id)).toEqual(['s2'])
  })
})

describe('missingRequiredQuestions', () => {
  const qn = questionnaire('qn1', [
    section('s1', [
      question({ id: 'q1', required: true }),
      question({ id: 'q2', required: true, multiple: true, evidence_type: 'select' }),
      question({ id: 'q3' }),
    ]),
  ])

  it('reports required questions left empty', () => {
    const missing = missingRequiredQuestions(
      { questions: { qn1: { s1: { q1: '', q2: [], q3: '' } } } },
      [qn],
      {},
    )
    expect(missing).toEqual(['questions.qn1.s1.q1', 'questions.qn1.s1.q2'])
  })

  it('accepts answered required questions', () => {
    const missing = missingRequiredQuestions(
      { questions: { qn1: { s1: { q1: 'yes', q2: ['a'] } } } },
      [qn],
      {},
    )
    expect(missing).toEqual([])
  })

  it('requires a conditional question once the preview reveals it', () => {
    const conditional = questionnaire('qn1', [
      section('s1', [question({ id: 'q4', required: true, hidden: true })]),
    ])
    const revealed: QuestionnairePreview = {
      sections: [{ id: 's1', questions: [{ id: 'q4', visible: true }] }],
    }
    expect(missingRequiredQuestions({ questions: {} }, [conditional], {})).toEqual([])
    expect(missingRequiredQuestions({ questions: {} }, [conditional], { qn1: revealed })).toEqual([
      'questions.qn1.s1.q4',
    ])
  })

  it('ignores required questions the preview has hidden', () => {
    const preview: QuestionnairePreview = {
      sections: [
        {
          id: 's1',
          questions: [
            { id: 'q1', visible: false },
            { id: 'q2', visible: false },
          ],
        },
      ],
    }
    const missing = missingRequiredQuestions({ questions: {} }, [qn], { qn1: preview })
    expect(missing).toEqual([])
  })
})

describe('buildFormDefaults', () => {
  const qn = questionnaire('qn1', [
    section('s1', [
      question({ id: 'q1' }),
      question({ id: 'q2', multiple: true, evidence_type: 'select' }),
      question({ id: 'q3', evidence_type: 'file' }),
    ]),
  ])

  it('pre-fills existing answers and seeds the rest as empty', () => {
    const defaults = buildFormDefaults({
      name: 'Fraud triage',
      description: 'Scores transactions',
      questionnaires: [qn],
      evidences: [{ questionId: 'q1', value: 'existing' }],
      customFieldValues: { cf1: 'Ops' },
    })
    expect(defaults.name).toBe('Fraud triage')
    expect(defaults.customFields).toEqual({ cf1: 'Ops' })
    expect(defaults.questions.qn1.s1).toEqual({ q1: 'existing', q2: '' })
  })

  it('does not submit an untouched, unanswered multi-select', () => {
    const defaults = buildFormDefaults({
      name: 'Fraud triage',
      description: '',
      questionnaires: [qn],
      evidences: [],
      customFieldValues: {},
    })
    expect(parseUseCaseForm(defaults).questions).toEqual([])
  })

  it('keeps an existing answer exactly as the API returned it', () => {
    const numeric = questionnaire('qn1', [section('s1', [question({ id: 'n', evidence_type: 'number' })])])
    const defaults = buildFormDefaults({
      name: '',
      description: '',
      questionnaires: [numeric],
      evidences: [{ questionId: 'n', value: 42 }],
      customFieldValues: {},
    })
    expect(defaults.questions.qn1.s1.n).toBe(42)
  })

  it('omits unsupported questions entirely', () => {
    const defaults = buildFormDefaults({
      name: '',
      description: '',
      questionnaires: [qn],
      evidences: [],
      customFieldValues: {},
    })
    expect(Object.keys(defaults.questions.qn1.s1)).not.toContain('q3')
  })
})
