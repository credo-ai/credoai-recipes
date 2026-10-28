// @vitest-environment jsdom
import { act } from 'react'
import { type Root, createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CredoClient } from '../api/credo'
import { CredoApiError } from '../api/credo'
import { type UseCasePreviewProps, resolvePreviewConfig } from '../config'
import { UseCasePreview } from './UseCasePreview'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const USE_CASE = {
  data: {
    id: 'uc-1',
    attributes: {
      name: 'Support Copilot',
      description: 'Helps agents',
      questionnaire_ids: ['INT+2', 'VEN+1'],
    },
  },
}

function questionnaireDoc(id: string, key: string, sectionTitle: string, questionId: string, text: string) {
  return {
    data: {
      id,
      attributes: {
        name: key,
        key,
        sections: [
          {
            id: 'sec-1',
            title: sectionTitle,
            questions: [
              { id: questionId, question: text, evidence_type: 'short_text' },
              { id: `${questionId}-blank`, question: `${text} (unanswered)`, evidence_type: 'short_text' },
            ],
          },
        ],
      },
    },
  }
}

const QUESTIONNAIRES: Record<string, unknown> = {
  'INT+2': questionnaireDoc('INT+2', 'INT', 'Intake basics', 'q-purpose', 'Purpose'),
  'VEN+1': questionnaireDoc('VEN+1', 'VEN', 'Vendor review', 'q-vendor', 'Vendor name'),
}

const EVIDENCES = [
  { attributes: { label: { key: 'q-purpose' }, data: { value: 'Summarize tickets' } } },
  { attributes: { label: { key: 'q-vendor' }, data: { value: 'Acme' } } },
]

const CUSTOM_FIELD_DEFS = [
  { id: 'cf-tier', attributes: { name: 'Tier', element_type: 'input' } },
  { id: 'cf-pii', attributes: { name: 'Uses PII', element_type: 'checkbox' } },
]
const CUSTOM_FIELD_VALUES = [
  { attributes: { custom_field_id: 'cf-tier', value: 'Gold' } },
  { attributes: { custom_field_id: 'cf-pii', value: true } },
]

function makeCredo() {
  return {
    getUseCase: vi.fn().mockResolvedValue(USE_CASE),
    getUseCaseEvidences: vi.fn().mockResolvedValue(EVIDENCES),
    getUseCaseCustomFields: vi.fn().mockResolvedValue(CUSTOM_FIELD_VALUES),
    listCustomFieldDefs: vi.fn().mockResolvedValue(CUSTOM_FIELD_DEFS),
    getQuestionnaire: vi.fn((id: string) => Promise.resolve(QUESTIONNAIRES[id])),
  }
}

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

async function renderPreview(
  props: Partial<UseCasePreviewProps> = {},
  credo = makeCredo(),
) {
  const emit = vi.fn()
  const { questionnaireOrder = '', ...attributes } = props
  const config = resolvePreviewConfig({ tenant: 'acme', useCaseId: 'uc-1', ...attributes })
  await act(async () => {
    root.render(
      <UseCasePreview
        config={config}
        credo={credo as unknown as CredoClient}
        useCaseId="uc-1"
        questionnaireOrder={questionnaireOrder}
        emit={emit}
      />,
    )
  })
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return { emit, credo }
}

// Block labels in render order (custom fields, sections, Models, Vendors).
const blockTitles = () =>
  [...container.querySelectorAll('.uppercase')].map((el) => el.textContent)
const text = () => container.textContent ?? ''

describe('UseCasePreview', () => {
  it('renders the hero, custom fields, then each questionnaire in attachment order', async () => {
    await renderPreview()

    expect(container.querySelector('[data-testid="tc-preview-title"]')?.textContent).toBe('Support Copilot')
    expect(container.querySelector('[data-testid="tc-preview-description"]')?.textContent).toBe('Helps agents')
    expect(blockTitles()).toEqual(['Additional information', 'Intake basics', 'Vendor review'])
    expect(text()).toContain('Summarize tickets')
    expect(text()).toContain('Gold')
    expect(text()).toContain('Yes')
    expect(text()).toContain('Powered by Credo AI')
  })

  it('omits unanswered questions', async () => {
    await renderPreview()

    expect(text()).not.toContain('(unanswered)')
  })

  it('orders questionnaires by questionnaire-order, on base id', async () => {
    await renderPreview({ questionnaireOrder: '["VEN"]' })

    expect(blockTitles()).toEqual(['Additional information', 'Vendor review', 'Intake basics'])
  })

  it('shows only allowed-questionnaires', async () => {
    await renderPreview({ allowedQuestionnaires: '["INT+2"]' })

    expect(blockTitles()).toEqual(['Additional information', 'Intake basics'])
    expect(text()).not.toContain('Acme')
  })

  it('shows only the custom fields named in custom-fields', async () => {
    await renderPreview({ customFields: '["Uses PII"]' })

    expect(text()).not.toContain('Gold')
    expect(text()).toContain('Uses PII')
  })

  it('hides every custom field for custom-fields="[]"', async () => {
    await renderPreview({ customFields: '[]' })

    expect(blockTitles()).toEqual(['Intake basics', 'Vendor review'])
  })

  it('warns and hides custom fields when custom-fields is malformed', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await renderPreview({ customFields: 'Tier' })

    expect(text()).not.toContain('Gold')
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('custom-fields is not a JSON array'))
  })

  it('warns about configured questionnaire ids that are not attached', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await renderPreview({ questionnaireOrder: '["GONE"]' })

    expect(warn).toHaveBeenCalledWith(expect.stringContaining('questionnaire-order: GONE'))
  })

  it('honours hide-header-text, use-case-fields and preview-footer-text', async () => {
    await renderPreview({
      hideHeaderText: 'true',
      useCaseFields: '["name"]',
      previewFooterText: '<b>Custom footer</b>',
    })

    expect(container.querySelector('[data-testid="tc-preview-title"]')).toBeNull()
    expect(container.querySelector('[data-testid="tc-preview-hero"]')).not.toBeNull()
    expect(text()).toContain('Name')
    expect(container.querySelector('b')?.textContent).toBe('Custom footer')
  })

  it('emits loaded with the shown questionnaires, in order', async () => {
    const { emit } = await renderPreview({ questionnaireOrder: '["VEN"]' })

    expect(emit).toHaveBeenCalledWith('loaded', { useCaseId: 'uc-1', questionnaireIds: ['VEN+1', 'INT+2'] })
  })

  it('shows an error with Retry on a failed load, and emits error', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const credo = makeCredo()
    credo.getUseCase.mockRejectedValueOnce(new CredoApiError('boom', 500))
    const { emit } = await renderPreview({}, credo)

    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Unable to load use case preview')
    expect(emit).toHaveBeenCalledWith('error', expect.objectContaining({ useCaseId: 'uc-1' }))

    await act(async () => {
      ;[...container.querySelectorAll('button')].find((b) => b.textContent === 'Retry')!.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(text()).toContain('Summarize tickets')
  })
})
