// @vitest-environment jsdom
import { act } from 'react'
import { type Root, createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CredoClient } from '../../api/credo'
import type { PlatformClient, QuestionnairePreview } from '../../api/platform'
import type { CredoStatus } from '../../types'
import { UseCaseEditForm } from './UseCaseEditForm'

// Behaviour checked against the Credo AI app's use-case edit form: load and
// pre-fill, conditional
// visibility from the questionnaire preview, the Update gate, the update
// payload, Reset, the header actions, and the host events.

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const USE_CASE = {
  data: {
    id: 'uc-1',
    attributes: { name: 'Support Copilot', description: 'Helps agents', questionnaire_ids: ['qn-1'] },
  },
}

const QUESTIONNAIRE = {
  data: {
    id: 'qn-1',
    attributes: {
      name: 'Intake',
      sections: [
        {
          id: 'sec-1',
          title: 'Basics',
          questions: [
            { id: 'q-purpose', question: 'Purpose', evidence_type: 'short_text', required: true },
            { id: 'q-pii', question: 'Uses PII?', evidence_type: 'select', select_options: ['Yes', 'No'] },
            { id: 'q-notes', question: 'Notes', evidence_type: 'text', required: true },
            { id: 'q-file', question: 'Attachment', evidence_type: 'file', required: true },
            { id: 'q-owner', question: 'Owner', evidence_type: 'user' },
          ],
        },
      ],
    },
  },
}

const EVIDENCES = [
  { attributes: { label: { key: 'q-purpose' }, data: { value: 'Summarize tickets' } } },
  { attributes: { label: { key: 'q-notes' }, data: { value: 'n/a' } } },
]

const CUSTOM_FIELD_DEFS = [{ id: 'cf-tier', attributes: { name: 'Tier', element_type: 'input' } }]
const CUSTOM_FIELD_VALUES = [{ attributes: { custom_field_id: 'cf-tier', value: 'Gold' } }]

const CONFIG = { user_email: 'u@example.com' } as CredoStatus

function makeClients(preview: QuestionnairePreview = { sections: [] }) {
  const credo = {
    getUseCase: vi.fn().mockResolvedValue(USE_CASE),
    getUseCaseEvidences: vi.fn().mockResolvedValue(EVIDENCES),
    getUseCaseCustomFields: vi.fn().mockResolvedValue(CUSTOM_FIELD_VALUES),
    listCustomFieldDefs: vi.fn().mockResolvedValue(CUSTOM_FIELD_DEFS),
    getQuestionnaire: vi.fn().mockResolvedValue(QUESTIONNAIRE),
    updateUseCaseFromForm: vi.fn().mockResolvedValue({ id: 'uc-1' }),
  }
  const platform = {
    getQuestionnairePreview: vi.fn().mockResolvedValue(preview),
    updateQuestionnairePreview: vi.fn().mockResolvedValue(preview),
  }
  return { credo, platform }
}

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  vi.useFakeTimers()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.useRealTimers()
})

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function renderForm(
  clients = makeClients(),
  props: Partial<{ appUrl: string; allowSubmit: boolean; hideCustomFields: boolean }> = {},
) {
  const emit = vi.fn()
  await act(async () => {
    root.render(
      <UseCaseEditForm
        config={CONFIG}
        credo={clients.credo as unknown as CredoClient}
        platform={clients.platform as unknown as PlatformClient}
        useCaseId="uc-1"
        appUrl={props.appUrl ?? ''}
        allowSubmit={props.allowSubmit ?? true}
        hideCustomFields={props.hideCustomFields ?? false}
        emit={emit}
      />,
    )
  })
  await flush()
  return { emit, ...clients }
}

const buttonByText = (label: string) =>
  [...container.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)
function fieldFor(labelText: string): HTMLInputElement | HTMLTextAreaElement {
  const label = [...container.querySelectorAll('label')].find((l) => l.textContent?.trim().startsWith(labelText))
  const field = label?.parentElement?.querySelector('input, textarea')
  if (!field) {
    throw new Error(`no field labelled "${labelText}"`)
  }
  return field as HTMLInputElement | HTMLTextAreaElement
}

async function type(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('UseCaseEditForm (Credo AI app parity)', () => {
  it('pre-fills name, description, answers and custom fields from the use case', async () => {
    await renderForm()

    expect(fieldFor('Name').value).toBe('Support Copilot')
    expect(fieldFor('Description').value).toBe('Helps agents')
    expect(fieldFor('Purpose').value).toBe('Summarize tickets')
    expect(fieldFor('Notes').value).toBe('n/a')
    expect(fieldFor('Tier').value).toBe('Gold')
  })

  it('renders neither file nor user questions', async () => {
    await renderForm()

    const text = container.textContent ?? ''
    expect(text).not.toContain('Attachment')
    expect(text).not.toContain('Owner')
  })

  it('disables Update while the name or a visible required question is empty', async () => {
    await renderForm()
    expect(buttonByText('Update')?.disabled).toBe(false)

    await type(fieldFor('Name'), '')
    expect(buttonByText('Update')?.disabled).toBe(true)
    await type(fieldFor('Name'), 'Support Copilot')

    await type(fieldFor('Purpose'), '')
    expect(buttonByText('Update')?.disabled).toBe(true)
  })

  it('hides questions the preview marks not visible, and stops requiring them', async () => {
    const clients = makeClients({
      sections: [{ id: 'sec-1', questions: [{ id: 'q-notes', visible: false }] }],
    })
    clients.credo.getUseCaseEvidences.mockResolvedValue([EVIDENCES[0]])
    await renderForm(clients)
    await flush(600)

    expect(container.textContent).not.toContain('Notes')
    expect(buttonByText('Update')?.disabled).toBe(false)
  })

  it('re-evaluates the preview with every answer for a questionnaire in one throttled POST', async () => {
    const { platform } = await renderForm()
    platform.updateQuestionnairePreview.mockClear()

    await type(fieldFor('Purpose'), 'a')
    await type(fieldFor('Purpose'), 'ab')
    await flush(600)

    expect(platform.updateQuestionnairePreview).toHaveBeenCalledOnce()
    expect(platform.updateQuestionnairePreview).toHaveBeenCalledWith('qn-1', [
      { section_id: 'sec-1', question_id: 'q-purpose', value: 'ab' },
      { section_id: 'sec-1', question_id: 'q-pii', value: '' },
      { section_id: 'sec-1', question_id: 'q-notes', value: 'n/a' },
    ])
  })

  it('sends the edited use case to the integration update with the user, then emits use_case_updated', async () => {
    const { credo, emit } = await renderForm()

    await type(fieldFor('Description'), 'Drafts replies')
    await act(async () => buttonByText('Update')!.click())
    await flush()

    expect(credo.updateUseCaseFromForm).toHaveBeenCalledWith('uc-1', {
      name: 'Support Copilot',
      description: 'Drafts replies',
      customFields: [{ customFieldId: 'cf-tier', value: 'Gold' }],
      questions: [
        { questionnaireId: 'qn-1', sectionId: 'sec-1', questionId: 'q-purpose', value: 'Summarize tickets' },
        { questionnaireId: 'qn-1', sectionId: 'sec-1', questionId: 'q-notes', value: 'n/a' },
      ],
      user: 'u@example.com',
    })
    expect(emit).toHaveBeenCalledWith('use_case_updated', { useCaseId: 'uc-1' })
  })

  it('Reset restores the loaded values', async () => {
    await renderForm()

    await type(fieldFor('Purpose'), 'changed')
    await act(async () => buttonByText('Reset')!.click())

    expect(fieldFor('Purpose').value).toBe('Summarize tickets')
  })

  it('emits form_values_changed with the use case id and the parsed values', async () => {
    const { emit } = await renderForm()

    await type(fieldFor('Name'), 'Renamed')

    expect(emit).toHaveBeenCalledWith(
      'form_values_changed',
      expect.objectContaining({ id: 'uc-1', name: 'Renamed' }),
    )
  })

  it('emits form_state_changed with isValid and isDirty', async () => {
    const { emit } = await renderForm()

    await type(fieldFor('Purpose'), '')

    expect(emit).toHaveBeenLastCalledWith(
      'form_state_changed',
      expect.objectContaining({ isValid: false, isDirty: true }),
    )
  })

  it('shows a conditional (hidden) question once the preview reveals it', async () => {
    const withConditional = structuredClone(QUESTIONNAIRE)
    withConditional.data.attributes.sections[0].questions.push({
      id: 'q-attest',
      question: 'Attestation',
      evidence_type: 'short_text',
      hidden: true,
    } as never)
    const clients = makeClients({
      sections: [{ id: 'sec-1', questions: [{ id: 'q-attest', visible: false }] }],
    })
    clients.credo.getQuestionnaire.mockResolvedValue(withConditional)
    clients.platform.updateQuestionnairePreview.mockResolvedValue({
      sections: [{ id: 'sec-1', questions: [{ id: 'q-attest', visible: true }] }],
    })
    await renderForm(clients)
    expect(container.textContent).not.toContain('Attestation')

    await type(fieldFor('Purpose'), 'EU rollout')
    await flush(600)

    expect(container.textContent).toContain('Attestation')
  })

  it('displays a saved numeric answer', async () => {
    const withNumber = structuredClone(QUESTIONNAIRE)
    withNumber.data.attributes.sections[0].questions.push({
      id: 'q-count',
      question: 'Headcount',
      evidence_type: 'number',
    } as never)
    const clients = makeClients()
    clients.credo.getQuestionnaire.mockResolvedValue(withNumber)
    clients.credo.getUseCaseEvidences.mockResolvedValue([
      ...EVIDENCES,
      { attributes: { label: { key: 'q-count' }, data: { value: 42 } } },
    ])
    await renderForm(clients)

    expect(fieldFor('Headcount').value).toBe('42')
  })

  it('does not submit an untouched, unanswered multi-select', async () => {
    const withMulti = structuredClone(QUESTIONNAIRE)
    withMulti.data.attributes.sections[0].questions.push({
      id: 'q-regions',
      question: 'Regions',
      evidence_type: 'select',
      multiple: true,
      select_options: ['EU', 'US'],
    } as never)
    const clients = makeClients()
    clients.credo.getQuestionnaire.mockResolvedValue(withMulti)
    const { credo } = await renderForm(clients)

    await act(async () => buttonByText('Update')!.click())
    await flush()

    const sent = credo.updateUseCaseFromForm.mock.calls[0][1].questions as { questionId: string }[]
    expect(sent.map((q) => q.questionId)).toEqual(['q-purpose', 'q-notes'])
  })

  it('honours app-url, allow-submit and hide-custom-fields', async () => {
    await renderForm(makeClients(), { appUrl: 'https://app.example', allowSubmit: false, hideCustomFields: true })

    const link = container.querySelector('a')
    expect(link?.getAttribute('href')).toBe('https://app.example/use-cases/uc-1')
    expect(link?.getAttribute('target')).toBe('_blank')
    expect(buttonByText('Update')).toBeUndefined()
    expect(container.textContent).not.toContain('Additional information')
  })
})
