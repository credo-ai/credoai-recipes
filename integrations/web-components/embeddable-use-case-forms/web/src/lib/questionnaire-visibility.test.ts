import { describe, expect, it } from 'vitest'

import {
  buildDefaultQuestionnaireVisibility,
  buildQuestionnaireVisibility,
  collectPreservedQuestionnaireVisibility,
  computeQuestionnaireHiddenQuestionIds,
  toggleQuestionnaireVisibility,
} from './questionnaire-visibility'
import { buildMergedSections, parseQuestionnaire } from './compose'
import questionnaireFixture from './fixtures/questionnaire.json'
import questionnaire2Fixture from './fixtures/questionnaire-2.json'

const sections = () =>
  buildMergedSections([parseQuestionnaire(questionnaireFixture), parseQuestionnaire(questionnaire2Fixture)])

describe('buildDefaultQuestionnaireVisibility', () => {
  it('defaults every questionnaire to visible', () => {
    expect(buildDefaultQuestionnaireVisibility(['qn-1', 'qn-2'])).toEqual([
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: true },
    ])
  })
})

describe('buildQuestionnaireVisibility', () => {
  it('keeps saved entries for still-live questionnaires', () => {
    const saved = [
      { questionnaire_id: 'qn-1', is_visible: false },
      { questionnaire_id: 'qn-2', is_visible: true },
    ]
    expect(buildQuestionnaireVisibility(saved, ['qn-1', 'qn-2'])).toEqual(saved)
  })

  it('drops stale saved entries and appends defaults for newly attached questionnaires', () => {
    const saved = [{ questionnaire_id: 'qn-removed', is_visible: false }]
    expect(buildQuestionnaireVisibility(saved, ['qn-1', 'qn-2'])).toEqual([
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: true },
    ])
  })

  it('defaults every questionnaire to visible when nothing was saved', () => {
    expect(buildQuestionnaireVisibility(null, ['qn-1'])).toEqual([
      { questionnaire_id: 'qn-1', is_visible: true },
    ])
  })
})

describe('collectPreservedQuestionnaireVisibility', () => {
  it('is a no-op when nothing is excluded', () => {
    expect(collectPreservedQuestionnaireVisibility([{ questionnaire_id: 'qn-1', is_visible: false }], [])).toEqual(
      [],
    )
  })

  it('carries forward only the excluded questionnaires entries', () => {
    const saved = [
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: false },
    ]
    expect(collectPreservedQuestionnaireVisibility(saved, ['qn-2'])).toEqual([
      { questionnaire_id: 'qn-2', is_visible: false },
    ])
  })
})

describe('toggleQuestionnaireVisibility', () => {
  it('flips only the targeted questionnaire', () => {
    const visibility = [
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: true },
    ]
    expect(toggleQuestionnaireVisibility(visibility, 'qn-2', false)).toEqual([
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: false },
    ])
    expect(visibility[1].is_visible).toBe(true) // input untouched
  })
})

describe('computeQuestionnaireHiddenQuestionIds', () => {
  it('hides only the questions belonging to a hidden questionnaire, even within a shared section', () => {
    const hidden = computeQuestionnaireHiddenQuestionIds(sections(), [
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: false },
    ])
    // sec-data is shared: qn-1 contributes q-sources/q-launch/q-doc, qn-2 contributes q-retention.
    expect(hidden.has('q-retention')).toBe(true)
    expect(hidden.has('q-sources')).toBe(false)
    expect(hidden.has('q-vendor-name')).toBe(true) // qn-2-only section
    expect(hidden.has('q-product')).toBe(false) // qn-1-only section
  })

  it('hides nothing when every questionnaire is visible', () => {
    const hidden = computeQuestionnaireHiddenQuestionIds(sections(), [
      { questionnaire_id: 'qn-1', is_visible: true },
      { questionnaire_id: 'qn-2', is_visible: true },
    ])
    expect(hidden.size).toBe(0)
  })
})
