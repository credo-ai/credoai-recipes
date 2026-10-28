import { describe, expect, it } from 'vitest'

import {
  applyQuestionnaireAllowList,
  buildMergedSections,
  buildSavePayload,
  composeCard,
  computeHiddenQuestionIds,
  deriveCardMode,
  mergeLayout,
  normalizeAnswers,
  NO_MATCHING_QUESTIONNAIRES_MESSAGE,
  parseLayoutJson,
  parseQuestionnaire,
  parseQuestionnaireVisibilityJson,
  parseUseCase,
  preserveExcludedLayout,
  readCustomFieldValue,
  resolveFieldId,
} from './compose'
import { parseCustomFieldDefs } from './custom-fields'
import customFieldsFixture from './fixtures/custom_fields.json'
import evidencesFixture from './fixtures/evidences.json'
import questionnaireFixture from './fixtures/questionnaire.json'
import questionnaire2Fixture from './fixtures/questionnaire-2.json'
import useCaseFixture from './fixtures/use_case.json'
import useCaseMultiFixture from './fixtures/use_case_multi.json'

const questionnaire = () => parseQuestionnaire(questionnaireFixture)
const questionnaire2 = () => parseQuestionnaire(questionnaire2Fixture)
const evidences = () => evidencesFixture.data

describe('parseQuestionnaire', () => {
  it('parses ids, name, version, and section order', () => {
    const q = questionnaire()
    expect(q.id).toBe('qn-1')
    expect(q.name).toBe('AI Transparency Intake')
    expect(q.version).toBe(12)
    expect(q.sections.map((s) => s.id)).toEqual(['sec-header', 'sec-data'])
    expect(q.sections.map((s) => s.title)).toEqual(['Header', 'Data Handling'])
  })

  it('filters hidden questions and parses question metadata', () => {
    const q = questionnaire()
    const header = q.sections[0]
    expect(header.questions.map((question) => question.id)).toEqual([
      'q-product',
      'q-desc',
    ])
    const product = header.questions[0]
    expect(product.evidence_type).toBe('select')
    expect(product.select_options).toEqual(['Alpha', 'Beta Suite', 'Gamma'])
    expect(product.multiple).toBe(false)
    expect(product.required).toBe(true)
    const sources = q.sections[1].questions[0]
    expect(sources.multiple).toBe(true)
    // missing select_options on a text question defaults to empty
    expect(header.questions[1].select_options).toEqual([])
  })

  it('keeps hidden questions, flagged, for the use-case forms', () => {
    const header = parseQuestionnaire(questionnaireFixture, { keepHidden: true }).sections[0]
    expect(header.questions.map((question) => [question.id, question.hidden])).toEqual([
      ['q-product', undefined],
      ['q-desc', undefined],
      ['q-hidden', true],
    ])
  })
})

describe('parseUseCase', () => {
  it('extracts display info and attached questionnaire ids', () => {
    const { useCase, questionnaireIds } = parseUseCase(useCaseFixture)
    expect(useCase.name).toBe('Support Copilot')
    expect(questionnaireIds).toEqual(['qn-1'])
  })

  it('extracts multiple attached questionnaire ids', () => {
    const { questionnaireIds } = parseUseCase(useCaseMultiFixture)
    expect(questionnaireIds).toEqual(['qn-1', 'qn-2'])
  })
})

describe('normalizeAnswers', () => {
  it('lets the last duplicate evidence win and ignores unknown questions', () => {
    const answers = normalizeAnswers(evidences(), [questionnaire()])
    expect(answers['q-product']).toBe('Beta Suite')
    // duplicate evidence for q-desc: later entry wins
    expect(answers['q-desc']).toBe(
      'Summarizes and drafts replies to support tickets.',
    )
    expect(answers['q-sources']).toEqual(['Public', 'Customer'])
    expect(answers).not.toHaveProperty('q-not-in-questionnaire')
  })

  it('resolves answers across more than one questionnaire', () => {
    const answers = normalizeAnswers(evidences(), [questionnaire(), questionnaire2()])
    expect(answers['q-product']).toBe('Beta Suite') // from qn-1
    expect(answers).not.toHaveProperty('q-retention') // qn-2 question, no evidence for it
  })
})

describe('deriveCardMode', () => {
  it('is setup until any answer exists', () => {
    expect(deriveCardMode({})).toBe('setup')
    expect(deriveCardMode({ q: null, q2: '', q3: [] })).toBe('setup')
    expect(deriveCardMode({ q: 'answer' })).toBe('edit')
  })
})

describe('custom fields', () => {
  it('resolves a field id by name and reads an inline value', () => {
    const fieldId = resolveFieldId(
      customFieldsFixture.defs,
      'Transparency Card Status',
    )
    expect(fieldId).toBe('cf-status')
    expect(resolveFieldId(customFieldsFixture.defs, 'Nope')).toBeNull()
    const value = readCustomFieldValue(customFieldsFixture.values_inline, fieldId)
    expect(value).toBe('Published to Acme.com')
    expect(readCustomFieldValue(customFieldsFixture.values_inline, null)).toBeNull()
  })

  it('reads a value via the custom_field relationship', () => {
    const value = readCustomFieldValue(
      customFieldsFixture.values_relationship,
      'cf-status',
    )
    expect(value).toBe('Draft')
  })
})

describe('parseLayoutJson', () => {
  it('parses the legacy web-component shape (flat, unversioned)', () => {
    const raw = readCustomFieldValue(customFieldsFixture.values_inline, 'cf-layout')
    const entries = parseLayoutJson(raw)
    expect(entries).toEqual([
      { section_id: 'sec-data', is_visible: false },
      { section_id: 'sec-stale', is_visible: true },
    ])
  })

  it("parses the superseded flat v3 shape, including per-question hides", () => {
    const raw = JSON.stringify({
      version: 3,
      visibility: [
        { section: 'sec-header', isVisible: true, hiddenQuestions: ['q-desc'] },
        { section: 'sec-data', isVisible: false },
      ],
    })
    expect(parseLayoutJson(raw)).toEqual([
      { section_id: 'sec-header', is_visible: true, hidden_question_ids: ['q-desc'] },
      { section_id: 'sec-data', is_visible: false },
    ])
  })

  it('parses the Credo AI app per-question questions[] array into hidden_question_ids', () => {
    const raw = JSON.stringify({
      visibility: [
        {
          section: 'sec-header',
          isVisible: true,
          questions: [
            { question: 'q-product', isVisible: false },
            { question: 'q-desc', isVisible: true },
          ],
        },
      ],
    })
    expect(parseLayoutJson(raw)).toEqual([
      { section_id: 'sec-header', is_visible: true, hidden_question_ids: ['q-product'] },
    ])
  })

  it('flattens and dedupes the superseded per-questionnaire-keyed v2 shape', () => {
    const raw = JSON.stringify({
      version: 2,
      questionnaires: {
        'qn-1': [{ section: 'sec-header', isVisible: true }],
        'qn-2': [
          { section: 'sec-header', isVisible: false }, // dup section id: first occurrence wins
          { section: 'sec-vendor', isVisible: true },
        ],
      },
    })
    expect(parseLayoutJson(raw)).toEqual([
      { section_id: 'sec-header', is_visible: true },
      { section_id: 'sec-vendor', is_visible: true },
    ])
  })

  it('treats no valid entries as no saved layout, skipping non-boolean isVisible', () => {
    expect(parseLayoutJson(JSON.stringify({ visibility: [] }))).toBeNull()
    expect(
      parseLayoutJson(
        JSON.stringify({
          visibility: [
            { section: 'sec-a', isVisible: 'false' },
            { section: 7, isVisible: true },
            { section: 'sec-b' },
            { section: 'sec-c', isVisible: true, questions: [{ question: 'q-1', isVisible: 'no' }] },
          ],
        }),
      ),
    ).toEqual([{ section_id: 'sec-c', is_visible: true }])
  })

  it('only accepts a JSON string, as upstream', () => {
    expect(parseLayoutJson({ visibility: [{ section: 'sec-a', isVisible: true }] })).toBeNull()
  })

  it('rejects garbage without throwing', () => {
    expect(parseLayoutJson('not json')).toBeNull()
    expect(parseLayoutJson(null)).toBeNull()
    expect(parseLayoutJson('["list"]')).toBeNull()
    expect(parseLayoutJson('{}')).toBeNull()
  })
})

describe('parseQuestionnaireVisibilityJson', () => {
  it("parses upstream's questionnaires array alongside section visibility", () => {
    const raw = JSON.stringify({
      visibility: [{ section: 'sec-header', isVisible: true }],
      questionnaires: [
        { questionnaire: 'qn-1', isVisible: true },
        { questionnaire: 'qn-2', isVisible: false },
      ],
    })
    expect(parseQuestionnaireVisibilityJson(raw)).toEqual([
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: false },
    ])
  })

  it('ignores the v2 layout shape, whose questionnaires key is an object', () => {
    const raw = JSON.stringify({ version: 2, questionnaires: { 'qn-1': [] } })
    expect(parseQuestionnaireVisibilityJson(raw)).toBeNull()
  })

  it("still parses the superseded questionnaireVisibility key", () => {
    const raw = JSON.stringify({
      version: 3,
      visibility: [{ section: 'sec-header', isVisible: true }],
      questionnaireVisibility: [
        { questionnaire: 'qn-1', isVisible: true },
        { questionnaire: 'qn-2', isVisible: false },
      ],
    })
    expect(parseQuestionnaireVisibilityJson(raw)).toEqual([
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: false },
    ])
  })

  it('returns null when the key is absent, rather than defaulting anything', () => {
    const raw = JSON.stringify({ version: 3, visibility: [] })
    expect(parseQuestionnaireVisibilityJson(raw)).toBeNull()
  })

  it('treats no valid entries as absent, skipping non-boolean isVisible', () => {
    expect(parseQuestionnaireVisibilityJson(JSON.stringify({ questionnaires: [] }))).toBeNull()
    expect(
      parseQuestionnaireVisibilityJson(
        JSON.stringify({ questionnaires: [{ questionnaire: 'qn-1' }, { questionnaire: 'qn-2', isVisible: false }] }),
      ),
    ).toEqual([{ questionnaire_id: 'qn-2', is_visible: false }])
  })

  it('rejects garbage without throwing', () => {
    expect(parseQuestionnaireVisibilityJson('not json')).toBeNull()
    expect(parseQuestionnaireVisibilityJson(null)).toBeNull()
  })
})

describe('buildMergedSections', () => {
  it('merges a shared section id across questionnaires, unioning questions and taking the last title', () => {
    const merged = buildMergedSections([questionnaire(), questionnaire2()])
    expect(merged.map((s) => s.id)).toEqual([
      'sec-header',
      'sec-data',
      'sec-internal-only',
      'sec-vendor',
    ])
    const data = merged.find((s) => s.id === 'sec-data')!
    expect(data.title).toBe('Data Handling (Vendor)') // qn-2's title wins (last occurrence)
    expect(data.questions.map((q) => q.id)).toEqual(['q-sources', 'q-launch', 'q-doc', 'q-retention'])
    expect(data.questions.map((q) => q.questionnaire_id)).toEqual([
      'qn-1',
      'qn-1',
      'qn-1',
      'qn-2',
    ])
  })

  it('tags every question with its origin questionnaire id', () => {
    const merged = buildMergedSections([questionnaire()])
    const header = merged.find((s) => s.id === 'sec-header')!
    expect(header.questions.every((q) => q.questionnaire_id === 'qn-1')).toBe(true)
  })
})

describe('mergeLayout', () => {
  it('filters stale saved sections and appends new ones visible', () => {
    const sections = buildMergedSections([questionnaire()])
    const saved = [
      { section_id: 'sec-data', is_visible: false },
      { section_id: 'sec-stale', is_visible: true },
    ]
    expect(mergeLayout(saved, sections)).toEqual([
      { section_id: 'sec-data', is_visible: false },
      { section_id: 'sec-header', is_visible: true },
    ])
    expect(mergeLayout(null, sections)).toEqual([
      { section_id: 'sec-header', is_visible: true },
      { section_id: 'sec-data', is_visible: true },
    ])
  })

  it('drops a section from the default layout when every question is unsupported', () => {
    const sections = buildMergedSections([questionnaire(), questionnaire2()])
    const layout = mergeLayout(null, sections)
    // sec-internal-only's only non-hidden question is a `file` type (unsupported)
    expect(layout.map((e) => e.section_id)).not.toContain('sec-internal-only')
    expect(layout.map((e) => e.section_id)).toEqual(['sec-header', 'sec-data', 'sec-vendor'])
  })
})

describe('computeHiddenQuestionIds', () => {
  const sections = buildMergedSections([questionnaire()])

  it('hides every question in a hidden section', () => {
    const layout = [
      { section_id: 'sec-header', is_visible: true },
      { section_id: 'sec-data', is_visible: false },
    ]
    const hidden = computeHiddenQuestionIds(layout, sections)
    expect(hidden.has('q-sources')).toBe(true)
    expect(hidden.has('q-launch')).toBe(true)
    expect(hidden.has('q-product')).toBe(false)
  })

  it('hides only the listed questions in a visible section', () => {
    const layout = [
      { section_id: 'sec-header', is_visible: true, hidden_question_ids: ['q-desc'] },
    ]
    const hidden = computeHiddenQuestionIds(layout, sections)
    expect(hidden.has('q-desc')).toBe(true)
    expect(hidden.has('q-product')).toBe(false)
  })
})

describe('applyQuestionnaireAllowList', () => {
  it('shows everything when the allow list is empty', () => {
    const qs = [questionnaire(), questionnaire2()]
    expect(applyQuestionnaireAllowList(qs, [])).toEqual({ included: qs, excluded: [] })
  })

  it('filters to the allowed subset and reports the rest as excluded', () => {
    const qs = [questionnaire(), questionnaire2()]
    const { included, excluded } = applyQuestionnaireAllowList(qs, ['qn-1'])
    expect(included.map((q) => q.id)).toEqual(['qn-1'])
    expect(excluded.map((q) => q.id)).toEqual(['qn-2'])
  })

  it('excludes everything when nothing matches', () => {
    const qs = [questionnaire()]
    const { included, excluded } = applyQuestionnaireAllowList(qs, ['qn-nope'])
    expect(included).toEqual([])
    expect(excluded.map((q) => q.id)).toEqual(['qn-1'])
  })
})

describe('preserveExcludedLayout', () => {
  it('is a no-op when nothing is excluded', () => {
    const live = [{ section_id: 'sec-header', is_visible: true }]
    expect(preserveExcludedLayout(live, null, [questionnaire()], [])).toBe(live)
  })

  it('carries forward a saved entry for a section that belongs only to an excluded questionnaire', () => {
    const live = [{ section_id: 'sec-header', is_visible: true }]
    const existingRaw = JSON.stringify({
      version: 3,
      visibility: [
        { section: 'sec-header', isVisible: true },
        { section: 'sec-vendor', isVisible: false }, // qn-2-only section, now excluded
      ],
    })
    const result = preserveExcludedLayout(
      live,
      existingRaw,
      [questionnaire()],
      [questionnaire2()],
    )
    expect(result).toEqual([
      { section_id: 'sec-header', is_visible: true },
      { section_id: 'sec-vendor', is_visible: false },
    ])
  })

  it('does not preserve a stale entry belonging to neither the included nor excluded set', () => {
    const live = [{ section_id: 'sec-header', is_visible: true }]
    const existingRaw = JSON.stringify({
      version: 3,
      visibility: [{ section: 'sec-removed-long-ago', isVisible: true }],
    })
    const result = preserveExcludedLayout(
      live,
      existingRaw,
      [questionnaire()],
      [questionnaire2()],
    )
    expect(result).toEqual(live)
  })
})

describe('composeCard', () => {
  it('composes a single-questionnaire card end to end', () => {
    const q = questionnaire()
    const card = composeCard({
      useCase: parseUseCase(useCaseFixture).useCase,
      attachedQuestionnaires: [q],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: 'Published to Acme.com',
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: readCustomFieldValue(customFieldsFixture.values_inline, 'cf-layout'),
      layoutFieldId: 'cf-layout',
      defaultLayoutRaw: null,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(card.card_mode).toBe('edit')
    expect(card.is_published).toBe(true)
    expect(card.layout_persistence_available).toBe(true)
    expect(card.layout.map((e) => e.section_id)).toEqual(['sec-data', 'sec-header'])
    expect(card.questionnaires[0].name).toBe('AI Transparency Intake')
    expect(card.mismatch_error).toBeNull()
  })

  it('merges sections from every attached questionnaire', () => {
    const card = composeCard({
      useCase: parseUseCase(useCaseMultiFixture).useCase,
      attachedQuestionnaires: [questionnaire(), questionnaire2()],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: null,
      layoutFieldId: null,
      defaultLayoutRaw: null,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(card.sections.map((s) => s.id)).toEqual([
      'sec-header',
      'sec-data',
      'sec-internal-only',
      'sec-vendor',
    ])
    expect(card.included_questionnaires.map((q) => q.id)).toEqual(['qn-1', 'qn-2'])
    expect(card.questionnaires.map((q) => q.id)).toEqual(['qn-1', 'qn-2'])
  })

  it('reports a mismatch error and keeps the unfiltered header data when the allow list excludes everything', () => {
    const card = composeCard({
      useCase: parseUseCase(useCaseFixture).useCase,
      attachedQuestionnaires: [questionnaire()],
      allowedQuestionnaireIds: ['qn-nope'],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: null,
      layoutFieldId: null,
      defaultLayoutRaw: null,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(card.mismatch_error).toBe(NO_MATCHING_QUESTIONNAIRES_MESSAGE)
    expect(card.sections).toEqual([])
    expect(card.excluded_questionnaire_names).toEqual(['AI Transparency Intake'])
    // header/status data reflects the unfiltered attached questionnaires
    expect(card.questionnaires.map((q) => q.id)).toEqual(['qn-1'])
    expect(card.use_case.name).toBe('Support Copilot')
  })

  it('falls back to defaultLayout only when no saved layout value exists yet', () => {
    const defaultLayoutRaw = JSON.stringify({
      version: 3,
      visibility: [{ section: 'sec-data', isVisible: false }],
    })
    const withDefault = composeCard({
      useCase: parseUseCase(useCaseFixture).useCase,
      attachedQuestionnaires: [questionnaire()],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: null, // no saved value yet
      layoutFieldId: 'cf-layout',
      defaultLayoutRaw,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(withDefault.layout).toEqual([
      { section_id: 'sec-data', is_visible: false },
      { section_id: 'sec-header', is_visible: true },
    ])

    // Once a real saved value exists, it wins over the default entirely.
    const withSaved = composeCard({
      useCase: parseUseCase(useCaseFixture).useCase,
      attachedQuestionnaires: [questionnaire()],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: JSON.stringify({ version: 3, visibility: [{ section: 'sec-header', isVisible: false }] }),
      layoutFieldId: 'cf-layout',
      defaultLayoutRaw,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(withSaved.layout).toEqual([
      { section_id: 'sec-header', is_visible: false },
      { section_id: 'sec-data', is_visible: true },
    ])

    // Malformed default JSON degrades to plain discovery order, never throws.
    const withMalformedDefault = composeCard({
      useCase: parseUseCase(useCaseFixture).useCase,
      attachedQuestionnaires: [questionnaire()],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: null,
      layoutFieldId: 'cf-layout',
      defaultLayoutRaw: 'not json',
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(withMalformedDefault.layout).toEqual([
      { section_id: 'sec-header', is_visible: true },
      { section_id: 'sec-data', is_visible: true },
    ])
  })

  it('has no mismatch error when there are simply no attached questionnaires', () => {
    const card = composeCard({
      useCase: parseUseCase(useCaseFixture).useCase,
      attachedQuestionnaires: [],
      allowedQuestionnaireIds: [],
      evidences: [],
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: null,
      layoutFieldId: null,
      defaultLayoutRaw: null,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(card.mismatch_error).toBeNull()
  })

  it('resolves configured custom fields and preview metadata rows', () => {
    const defs = parseCustomFieldDefs([
      { id: 'cf-owner', attributes: { name: 'Business Owner', element_type: 'input' } },
    ])
    const card = composeCard({
      useCase: parseUseCase(useCaseFixture).useCase,
      attachedQuestionnaires: [questionnaire()],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: null,
      layoutFieldId: null,
      defaultLayoutRaw: null,
      customFieldDefs: defs,
      customFieldValues: [{ attributes: { custom_field_id: 'cf-owner', value: 'Jane Doe' } }],
      configuredCustomFieldNames: ['Business Owner'],
      useCaseFieldNames: ['name', 'description'],
    })
    expect(card.custom_fields).toEqual([{ def: defs[0], value: 'Jane Doe' }])
    expect(card.preview_metadata_rows).toEqual([
      { key: 'name', label: 'Name', value: 'Support Copilot' },
      { key: 'description', label: 'Description', value: 'AI assistant for support agents' },
    ])
  })

  it('defaults every included questionnaire to visible when nothing was saved', () => {
    const card = composeCard({
      useCase: parseUseCase(useCaseMultiFixture).useCase,
      attachedQuestionnaires: [questionnaire(), questionnaire2()],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw: null,
      layoutFieldId: null,
      defaultLayoutRaw: null,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(card.questionnaire_visibility).toEqual([
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: true },
    ])
  })

  it('reconciles a saved questionnaireVisibility entry, dropping stale ids and appending new ones', () => {
    const layoutRaw = JSON.stringify({
      version: 3,
      visibility: [],
      questionnaireVisibility: [
        { questionnaire: 'qn-1', isVisible: false },
        { questionnaire: 'qn-removed', isVisible: false },
      ],
    })
    const card = composeCard({
      useCase: parseUseCase(useCaseMultiFixture).useCase,
      attachedQuestionnaires: [questionnaire(), questionnaire2()],
      allowedQuestionnaireIds: [],
      evidences: evidences(),
      cardStatus: null,
      publishedStatusValue: 'Published to Acme.com',
      layoutRaw,
      layoutFieldId: 'cf-layout',
      defaultLayoutRaw: null,
      customFieldDefs: [],
      customFieldValues: [],
      configuredCustomFieldNames: [],
      useCaseFieldNames: [],
    })
    expect(card.questionnaire_visibility).toEqual([
      { questionnaire_id: 'qn-1', is_visible: false },
      { questionnaire_id: 'qn-2', is_visible: true },
    ])
  })

  describe('default-order-and-visibility fallback (Credo AI app rules)', () => {
    const compose = (layoutRaw: string | null, defaultLayoutRaw: string | null) =>
      composeCard({
        useCase: parseUseCase(useCaseMultiFixture).useCase,
        attachedQuestionnaires: [questionnaire(), questionnaire2()],
        allowedQuestionnaireIds: [],
        evidences: evidences(),
        cardStatus: null,
        publishedStatusValue: 'Published to Acme.com',
        layoutRaw,
        layoutFieldId: 'cf-layout',
        defaultLayoutRaw,
        customFieldDefs: [],
        customFieldValues: [],
        configuredCustomFieldNames: [],
        useCaseFieldNames: [],
      })
    const defaultLayoutRaw = JSON.stringify({
      visibility: [{ section: 'sec-data', isVisible: false }],
      questionnaires: [{ questionnaire: 'qn-2', isVisible: false }],
    })
    const sectionOf = (card: ReturnType<typeof compose>, id: string) =>
      card.layout.find((e) => e.section_id === id)
    const questionnaireOf = (card: ReturnType<typeof compose>, id: string) =>
      card.questionnaire_visibility.find((e) => e.questionnaire_id === id)

    it('applies the whole default when the saved value is empty', () => {
      const card = compose(JSON.stringify({ visibility: [], questionnaires: [] }), defaultLayoutRaw)
      expect(sectionOf(card, 'sec-data')?.is_visible).toBe(false)
      expect(questionnaireOf(card, 'qn-2')?.is_visible).toBe(false)
    })

    it("doesn't apply the default's questionnaires when saved sections exist", () => {
      const card = compose(
        JSON.stringify({ visibility: [{ section: 'sec-header', isVisible: true }], questionnaires: [] }),
        defaultLayoutRaw,
      )
      expect(questionnaireOf(card, 'qn-2')?.is_visible).toBe(true)
      expect(sectionOf(card, 'sec-data')?.is_visible).toBe(true)
    })

    it("still applies the default's sections when only questionnaires are saved", () => {
      const card = compose(
        JSON.stringify({ visibility: [], questionnaires: [{ questionnaire: 'qn-1', isVisible: false }] }),
        defaultLayoutRaw,
      )
      expect(sectionOf(card, 'sec-data')?.is_visible).toBe(false)
      expect(questionnaireOf(card, 'qn-1')?.is_visible).toBe(false)
      expect(questionnaireOf(card, 'qn-2')?.is_visible).toBe(true)
    })
  })
})

describe('buildSavePayload', () => {
  it('maps questions to their origin questionnaire/section and drops invalid answers', () => {
    const sections = buildMergedSections([questionnaire()])
    const payload = buildSavePayload({
      sections,
      answers: {
        'q-product': 'Alpha',
        'q-desc': 'Does things.',
        'q-sources': ['Public', ''],
        'q-launch': '2026-07-01',
        'q-doc': 'should-be-dropped', // file question: unsupported
        'q-unknown': 'forged', // not in any questionnaire
        'q-empty': '',
      },
      hiddenQuestionIds: new Set(),
      layoutToPersist: null,
      layoutFieldId: null,
      customFields: [],
      user: 'dev@customer.com',
    })
    expect(payload.user).toBe('dev@customer.com')
    const byId = Object.fromEntries(payload.questions.map((q) => [q.question_id, q]))
    expect(Object.keys(byId).sort()).toEqual([
      'q-desc',
      'q-launch',
      'q-product',
      'q-sources',
    ])
    expect(byId['q-product'].section_id).toBe('sec-header')
    expect(byId['q-sources'].section_id).toBe('sec-data')
    expect(byId['q-sources'].value).toEqual(['Public']) // empty element removed
    expect(payload.questions.every((q) => q.questionnaire_id === 'qn-1')).toBe(true)
    expect(payload).not.toHaveProperty('custom_fields')
  })

  it('omits an unanswered question whose form value is undefined', () => {
    // A question with no stored evidence reaches the form as `undefined`;
    // stringifying it wrote the literal "undefined" upstream (and 422'd on a
    // select, whose options don't include it).
    const payload = buildSavePayload({
      sections: buildMergedSections([questionnaire()]),
      answers: { 'q-product': undefined, 'q-desc': 'Does things.' },
      hiddenQuestionIds: new Set(),
      layoutToPersist: null,
      layoutFieldId: null,
      customFields: [],
      user: 'dev@customer.com',
    })
    expect(payload.questions.map((q) => q.question_id)).toEqual(['q-desc'])
  })

  it('always includes an emptied multi-select as value: [] rather than omitting it', () => {
    const sections = buildMergedSections([questionnaire()])
    const payload = buildSavePayload({
      sections,
      answers: { 'q-sources': [] },
      hiddenQuestionIds: new Set(),
      layoutToPersist: null,
      layoutFieldId: null,
      customFields: [],
      user: 'dev@customer.com',
    })
    expect(payload.questions).toEqual([
      { questionnaire_id: 'qn-1', section_id: 'sec-data', question_id: 'q-sources', value: [] },
    ])
  })

  it('excludes hidden questions even when answered', () => {
    const sections = buildMergedSections([questionnaire()])
    const payload = buildSavePayload({
      sections,
      answers: { 'q-product': 'Alpha', 'q-desc': 'Hidden answer' },
      hiddenQuestionIds: new Set(['q-desc']),
      layoutToPersist: null,
      layoutFieldId: null,
      customFields: [],
      user: 'dev@customer.com',
    })
    expect(payload.questions.map((q) => q.question_id)).toEqual(['q-product'])
  })

  it('routes each question to the correct questionnaire when sections are merged', () => {
    const sections = buildMergedSections([questionnaire(), questionnaire2()])
    const payload = buildSavePayload({
      sections,
      answers: { 'q-sources': ['Public'], 'q-retention': '30 days' },
      hiddenQuestionIds: new Set(),
      layoutToPersist: null,
      layoutFieldId: null,
      customFields: [],
      user: 'dev@customer.com',
    })
    const byId = Object.fromEntries(payload.questions.map((q) => [q.question_id, q]))
    expect(byId['q-sources'].questionnaire_id).toBe('qn-1')
    expect(byId['q-retention'].questionnaire_id).toBe('qn-2')
    expect(byId['q-sources'].section_id).toBe('sec-data')
    expect(byId['q-retention'].section_id).toBe('sec-data')
  })

  it('never includes name or description', () => {
    const sections = buildMergedSections([questionnaire()])
    const payload = buildSavePayload({
      sections,
      answers: {},
      hiddenQuestionIds: new Set(),
      layoutToPersist: null,
      layoutFieldId: null,
      customFields: [],
      user: 'dev@customer.com',
    })
    expect(payload).not.toHaveProperty('name')
    expect(payload).not.toHaveProperty('description')
  })

  it("serializes the layout to persist in upstream's shape", () => {
    const sections = buildMergedSections([questionnaire()])
    const payload = buildSavePayload({
      sections,
      answers: {},
      hiddenQuestionIds: new Set(),
      layoutToPersist: [
        { section_id: 'sec-data', is_visible: false, hidden_question_ids: ['q-launch'] },
        { section_id: 'sec-header', is_visible: true },
      ],
      layoutFieldId: 'cf-layout',
      customFields: [],
      user: 'dev@customer.com',
    })
    const field = payload.custom_fields?.[0]
    expect(field?.custom_field_id).toBe('cf-layout')
    expect(JSON.parse(field?.value as string)).toEqual({
      visibility: [
        {
          section: 'sec-data',
          isVisible: false,
          questions: [
            { question: 'q-sources', isVisible: true },
            { question: 'q-launch', isVisible: false },
          ],
        },
        { section: 'sec-header', isVisible: true },
      ],
      questionnaires: [],
    })
  })

  it('keeps hidden ids of a section with no live rows (preserved, allow-list-excluded)', () => {
    const payload = buildSavePayload({
      sections: buildMergedSections([questionnaire()]),
      answers: {},
      hiddenQuestionIds: new Set(),
      layoutToPersist: [{ section_id: 'sec-gone', is_visible: true, hidden_question_ids: ['q-x'] }],
      layoutFieldId: 'cf-layout',
      customFields: [],
      user: 'dev@customer.com',
    })
    expect(JSON.parse(payload.custom_fields?.[0]?.value as string).visibility).toEqual([
      { section: 'sec-gone', isVisible: true, questions: [{ question: 'q-x', isVisible: false }] },
    ])
  })

  it('persists questionnaire visibility under the questionnaires key of the same blob', () => {
    const sections = buildMergedSections([questionnaire()])
    const payload = buildSavePayload({
      sections,
      answers: {},
      hiddenQuestionIds: new Set(),
      layoutToPersist: [{ section_id: 'sec-header', is_visible: true }],
      layoutFieldId: 'cf-layout',
      questionnaireVisibilityToPersist: [{ questionnaire_id: 'qn-2', is_visible: false }],
      customFields: [],
      user: 'dev@customer.com',
    })
    const field = payload.custom_fields?.[0]
    expect(JSON.parse(field?.value as string)).toEqual({
      visibility: [{ section: 'sec-header', isVisible: true }],
      questionnaires: [{ questionnaire: 'qn-2', isVisible: false }],
    })
  })

  it('writes an empty questionnaires array when nothing is persisted, as upstream does', () => {
    const sections = buildMergedSections([questionnaire()])
    const payload = buildSavePayload({
      sections,
      answers: {},
      hiddenQuestionIds: new Set(),
      layoutToPersist: [{ section_id: 'sec-header', is_visible: true }],
      layoutFieldId: 'cf-layout',
      customFields: [],
      user: 'dev@customer.com',
    })
    const field = payload.custom_fields?.[0]
    expect(JSON.parse(field?.value as string).questionnaires).toEqual([])
  })

  it('always writes every configured custom field, sending null for a cleared value', () => {
    const sections = buildMergedSections([questionnaire()])
    const defs = parseCustomFieldDefs([
      { id: 'cf-owner', attributes: { name: 'Business Owner', element_type: 'input' } },
      { id: 'cf-region', attributes: { name: 'Deployment Region', element_type: 'select' } },
    ])
    const payload = buildSavePayload({
      sections,
      answers: { 'cf:cf-owner': 'Jane Doe', 'cf:cf-region': '' },
      hiddenQuestionIds: new Set(),
      layoutToPersist: null,
      layoutFieldId: null,
      customFields: defs,
      user: 'dev@customer.com',
    })
    expect(payload.custom_fields).toEqual([
      { custom_field_id: 'cf-owner', value: 'Jane Doe' },
      { custom_field_id: 'cf-region', value: null },
    ])
  })

  it('combines the layout entry and configured custom fields in one array', () => {
    const sections = buildMergedSections([questionnaire()])
    const defs = parseCustomFieldDefs([
      { id: 'cf-owner', attributes: { name: 'Business Owner', element_type: 'input' } },
    ])
    const payload = buildSavePayload({
      sections,
      answers: { 'cf:cf-owner': 'Jane Doe' },
      hiddenQuestionIds: new Set(),
      layoutToPersist: [{ section_id: 'sec-header', is_visible: true }],
      layoutFieldId: 'cf-layout',
      customFields: defs,
      user: 'dev@customer.com',
    })
    expect(payload.custom_fields?.map((f) => f.custom_field_id)).toEqual(['cf-layout', 'cf-owner'])
  })
})
