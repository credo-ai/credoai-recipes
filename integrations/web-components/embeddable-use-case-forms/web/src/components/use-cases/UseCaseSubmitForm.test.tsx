// @vitest-environment jsdom
import { act } from 'react'
import { type Root, createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CredoClient } from '../../api/credo'
import type { PlatformClient, QuestionnairePreview } from '../../api/platform'
import type { CredoStatus, QuestionnaireSummary } from '../../types'
import { UseCaseSubmitForm } from './UseCaseSubmitForm'

// Behaviour checked against the Credo AI app's use-case create and intake
// forms: questionnaire
// selection, the Submit gate, the create payload, Reset, conditional
// visibility (create) versus a static form (intake), and the host events.

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const AVAILABLE: QuestionnaireSummary[] = [
  { id: 'qn-1', key: 'INT', name: 'Intake', version: 1 },
  { id: 'qn-2', key: 'VEN', name: 'Vendor', version: 1 },
]

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
            {
              id: 'q-regions',
              question: 'Regions',
              evidence_type: 'select',
              multiple: true,
              select_options: ['EU', 'US'],
            },
            { id: 'q-file', question: 'Attachment', evidence_type: 'file', required: true },
            { id: 'q-owner', question: 'Owner', evidence_type: 'user' },
            { id: 'q-attest', question: 'Attestation', evidence_type: 'short_text', hidden: true },
          ],
        },
      ],
    },
  },
}

const CUSTOM_FIELD_DEFS = [{ id: 'cf-tier', attributes: { name: 'Tier', element_type: 'input' } }]

const CONFIG = { user_email: 'u@example.com' } as CredoStatus

function makeClients(preview: QuestionnairePreview = { sections: [] }) {
  const credo = {
    listCustomFieldDefs: vi.fn().mockResolvedValue(CUSTOM_FIELD_DEFS),
    getQuestionnaire: vi.fn().mockResolvedValue(QUESTIONNAIRE),
    createUseCase: vi.fn().mockResolvedValue({ id: 'uc-new' }),
  }
  const platform = {
    listCreatableQuestionnaires: vi.fn().mockResolvedValue(AVAILABLE),
    getQuestionnairePreview: vi.fn().mockResolvedValue(preview),
    updateQuestionnairePreview: vi.fn().mockResolvedValue(preview),
  }
  return { credo, platform }
}

type Props = Partial<{
  questionnaireId: string
  appUrl: string
  source: string
  allowSubmit: boolean
  hideCustomFields: boolean
  disableQuestionnaireSelection: boolean
  conditional: boolean
}>

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

async function renderForm(clients = makeClients(), props: Props = {}) {
  const emit = vi.fn()
  await act(async () => {
    root.render(
      <UseCaseSubmitForm
        config={CONFIG}
        credo={clients.credo as unknown as CredoClient}
        platform={clients.platform as unknown as PlatformClient}
        questionnaireId={props.questionnaireId ?? 'qn-1'}
        appUrl={props.appUrl ?? ''}
        source={props.source ?? ''}
        allowSubmit={props.allowSubmit ?? true}
        hideCustomFields={props.hideCustomFields ?? false}
        disableQuestionnaireSelection={props.disableQuestionnaireSelection ?? true}
        conditional={props.conditional ?? true}
        emit={emit}
      />,
    )
  })
  await flush()
  return { emit, ...clients }
}

const buttonByText = (label: string) =>
  [...container.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)
const picker = () => container.querySelector<HTMLSelectElement>('#questionnaire-id')!

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

async function choose(select: HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, value)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

describe('UseCaseSubmitForm (Credo AI app parity)', () => {
  it('lists the creatable questionnaires and locks the configured one', async () => {
    await renderForm()

    expect([...picker().options].map((o) => o.value)).toEqual(['', 'qn-1', 'qn-2'])
    expect(picker().value).toBe('qn-1')
    expect(picker().disabled).toBe(true)
  })

  it('lets the submitter pick when selection is not disabled', async () => {
    const { credo } = await renderForm(makeClients(), { questionnaireId: '', disableQuestionnaireSelection: false })
    expect(picker().disabled).toBe(false)
    expect(container.textContent).not.toContain('Purpose')

    await choose(picker(), 'qn-1')
    await flush()

    expect(credo.getQuestionnaire).toHaveBeenCalledWith('qn-1')
    expect(container.textContent).toContain('Purpose')
  })

  it('auto-selects the only creatable questionnaire', async () => {
    const clients = makeClients()
    clients.platform.listCreatableQuestionnaires.mockResolvedValue([AVAILABLE[0]])
    await renderForm(clients, { questionnaireId: '', disableQuestionnaireSelection: false })

    expect(picker().value).toBe('qn-1')
  })

  it('auto-selects the only creatable questionnaire even when another is configured', async () => {
    const clients = makeClients()
    clients.platform.listCreatableQuestionnaires.mockResolvedValue([AVAILABLE[0]])
    await renderForm(clients, { questionnaireId: 'qn-2', disableQuestionnaireSelection: false })

    expect(picker().value).toBe('qn-1')
  })

  it('swaps a configured older version for the current one with the same key', async () => {
    const { credo } = await renderForm(makeClients(), { questionnaireId: 'INT+0' })
    await flush()

    expect(picker().value).toBe('qn-1')
    expect(credo.getQuestionnaire).toHaveBeenCalledWith('qn-1')
    expect(credo.getQuestionnaire).not.toHaveBeenCalledWith('INT+0')
    expect(container.textContent).not.toContain('Invalid configuration')
    expect(container.textContent).toContain('Purpose')
  })

  it('keeps the swapped-in current version across Reset', async () => {
    await renderForm(makeClients(), { questionnaireId: 'INT+0' })
    await flush()

    await act(async () => buttonByText('Reset')!.click())
    await flush()

    expect(picker().value).toBe('qn-1')
    expect(container.textContent).toContain('Purpose')
  })

  it('warns, and renders no questions, when the locked questionnaire is not creatable', async () => {
    const { credo } = await renderForm(makeClients(), { questionnaireId: 'qn-gone' })
    await flush()

    expect(container.textContent).toContain('Invalid configuration')
    expect(credo.getQuestionnaire).not.toHaveBeenCalled()
    expect(container.textContent).not.toContain('Purpose')
  })

  it('renders neither file nor user questions', async () => {
    await renderForm()

    const text = container.textContent ?? ''
    expect(text).not.toContain('Attachment')
    expect(text).not.toContain('Owner')
  })

  it('disables Submit until the name and the visible required questions are filled', async () => {
    await renderForm()
    expect(buttonByText('Submit')?.disabled).toBe(true)

    await type(fieldFor('Name'), 'Support Copilot')
    expect(buttonByText('Submit')?.disabled).toBe(true)

    await type(fieldFor('Purpose'), 'Summarize tickets')
    expect(buttonByText('Submit')?.disabled).toBe(false)
  })

  it('creates the use case with the questionnaire, user and source, emits use_case_created, then resets', async () => {
    const { credo, emit } = await renderForm(makeClients(), { source: 'portal', appUrl: 'https://app.example' })

    await type(fieldFor('Name'), 'Support Copilot')
    await type(fieldFor('Purpose'), 'Summarize tickets')
    await type(fieldFor('Tier'), 'Gold')
    await act(async () => buttonByText('Submit')!.click())
    await flush()

    expect(credo.createUseCase).toHaveBeenCalledWith({
      name: 'Support Copilot',
      description: '',
      customFields: [{ customFieldId: 'cf-tier', value: 'Gold' }],
      questions: [
        { questionnaireId: 'qn-1', sectionId: 'sec-1', questionId: 'q-purpose', value: 'Summarize tickets' },
      ],
      questionnaireIds: ['qn-1'],
      user: 'u@example.com',
      source: 'portal',
    })
    expect(emit).toHaveBeenCalledWith('use_case_created', { useCaseId: 'uc-new' })
    expect(container.textContent).toContain('https://app.example/use-cases/uc-new')
    expect(fieldFor('Name').value).toBe('')
    expect(fieldFor('Purpose').value).toBe('')
  })

  it('Reset clears the answers but keeps the configured questionnaire', async () => {
    await renderForm()

    await type(fieldFor('Name'), 'Support Copilot')
    await type(fieldFor('Purpose'), 'x')
    await act(async () => buttonByText('Reset')!.click())

    expect(fieldFor('Name').value).toBe('')
    expect(fieldFor('Purpose').value).toBe('')
    expect(picker().value).toBe('qn-1')
  })

  it('clears the answers when the questionnaire changes', async () => {
    const vendor = structuredClone(QUESTIONNAIRE)
    vendor.data.id = 'qn-2'
    const clients = makeClients()
    clients.credo.getQuestionnaire.mockImplementation(async (id: string) => (id === 'qn-2' ? vendor : QUESTIONNAIRE))
    const { emit } = await renderForm(clients, { disableQuestionnaireSelection: false })

    await type(fieldFor('Purpose'), 'typed against qn-1')
    await choose(picker(), 'qn-2')
    await flush()

    expect(fieldFor('Purpose').value).toBe('')
    const last = emit.mock.calls.filter(([e]) => e === 'form_values_changed').at(-1)![1]
    expect(last.questions).toEqual([])
  })

  it('emits form_values_changed with the parsed values and the questionnaire ids', async () => {
    const { emit } = await renderForm()

    await type(fieldFor('Name'), 'Renamed')

    expect(emit).toHaveBeenCalledWith(
      'form_values_changed',
      expect.objectContaining({ name: 'Renamed', questionnaireIds: ['qn-1'] }),
    )
  })

  it('emits form_state_changed with isValid and isDirty', async () => {
    const { emit } = await renderForm()

    await type(fieldFor('Name'), 'Support Copilot')

    expect(emit).toHaveBeenLastCalledWith(
      'form_state_changed',
      expect.objectContaining({ isValid: false, isDirty: true }),
    )
  })

  it('does not submit an untouched multi-select', async () => {
    const { credo } = await renderForm()

    await type(fieldFor('Name'), 'Support Copilot')
    await type(fieldFor('Purpose'), 'x')
    await act(async () => buttonByText('Submit')!.click())
    await flush()

    const sent = credo.createUseCase.mock.calls[0][0].questions as { questionId: string }[]
    expect(sent.map((q) => q.questionId)).toEqual(['q-purpose'])
  })

  it('honours allow-submit and hide-custom-fields', async () => {
    await renderForm(makeClients(), { allowSubmit: false, hideCustomFields: true })

    expect(buttonByText('Submit')).toBeUndefined()
    expect(container.textContent).not.toContain('Tier')
  })



  describe('create (conditional)', () => {
    it('fetches the initial preview, then re-evaluates every answer in one throttled POST', async () => {
      const { platform } = await renderForm()
      expect(platform.getQuestionnairePreview).toHaveBeenCalledWith('qn-1')
      platform.updateQuestionnairePreview.mockClear()

      await type(fieldFor('Purpose'), 'a')
      await type(fieldFor('Purpose'), 'ab')
      await flush(600)

      expect(platform.updateQuestionnairePreview).toHaveBeenCalledOnce()
      expect(platform.updateQuestionnairePreview.mock.calls[0][0]).toBe('qn-1')
      expect(platform.updateQuestionnairePreview.mock.calls[0][1]).toContainEqual({
        section_id: 'sec-1',
        question_id: 'q-purpose',
        value: 'ab',
      })
    })

    it('hides questions the preview marks not visible', async () => {
      await renderForm(makeClients({ sections: [{ id: 'sec-1', questions: [{ id: 'q-pii', visible: false }] }] }))

      expect(container.textContent).not.toContain('Uses PII?')
    })

    it('shows a conditional (hidden) question once the preview reveals it', async () => {
      const clients = makeClients()
      clients.platform.updateQuestionnairePreview.mockResolvedValue({
        sections: [{ id: 'sec-1', questions: [{ id: 'q-attest', visible: true }] }],
      })
      await renderForm(clients)
      expect(container.textContent).not.toContain('Attestation')

      await type(fieldFor('Purpose'), 'EU rollout')
      await flush(600)

      expect(container.textContent).toContain('Attestation')
    })
  })

  describe('intake (static)', () => {
    it('renders every non-hidden question and never calls the preview', async () => {
      const { platform } = await renderForm(
        makeClients({ sections: [{ id: 'sec-1', questions: [{ id: 'q-pii', visible: false }] }] }),
        { conditional: false },
      )
      await type(fieldFor('Purpose'), 'x')
      await flush(600)

      expect(container.textContent).toContain('Uses PII?')
      expect(container.textContent).not.toContain('Attestation')
      expect(platform.getQuestionnairePreview).not.toHaveBeenCalled()
      expect(platform.updateQuestionnairePreview).not.toHaveBeenCalled()
    })
  })
})
