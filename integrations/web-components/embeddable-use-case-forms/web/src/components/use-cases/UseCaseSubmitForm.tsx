import { useEffect, useMemo, useState } from 'react'
import { FormProvider, useForm, useWatch } from 'react-hook-form'

import { errorMessage } from '../../api/errors'
import type { CredoClient } from '../../api/credo'
import type { PlatformClient } from '../../api/platform'
import { questionnaireBaseId } from '../../lib/blocking-questionnaires'
import { parseQuestionnaire } from '../../lib/compose'
import { parseCustomFieldDefs } from '../../lib/custom-fields'
import {
  type UseCaseFormValues,
  missingRequiredQuestions,
  parseUseCaseForm,
} from '../../lib/use-case-form'
import type { CredoStatus, CustomFieldDef, Questionnaire, QuestionnaireSummary } from '../../types'
import { ElementLoading } from '../ElementShell'
import {
  FormBanner,
  FormCard,
  FormHeader,
  FormSection,
  PrimaryButton,
  SecondaryButton,
} from './FormLayout'
import { QuestionnairePicker } from './QuestionnairePicker'
import { UseCaseInformationFields } from './UseCaseInformationFields'
import { UseCaseCustomFields } from './UseCaseCustomFields'
import { UseCaseQuestions } from './UseCaseQuestions'
import { useQuestionnairePreview } from './useQuestionnairePreview'

export type SubmitFormEvent =
  | 'use_case_created'
  | 'form_values_changed'
  | 'form_state_changed'
  | 'error'

interface UseCaseSubmitFormProps {
  config: CredoStatus
  credo: CredoClient
  platform: PlatformClient
  questionnaireId: string
  appUrl: string
  source: string
  allowSubmit: boolean
  hideCustomFields: boolean
  disableQuestionnaireSelection: boolean
  // The one behavioural difference between the intake and create forms: the
  // create form re-evaluates the questionnaire's conditional logic against the
  // answers as they're typed; the intake form renders every question up front.
  conditional: boolean
  emit: (event: SubmitFormEvent, detail: unknown) => void
}

// The submit forms carry one field the shared shape doesn't: which
// questionnaire the use case is being created against.
interface SubmitFormValues extends UseCaseFormValues {
  questionnaireId: string
}

const EMPTY_VALUES: Omit<SubmitFormValues, 'questionnaireId'> = {
  name: '',
  description: '',
  customFields: {},
  questions: {},
}

// Which questionnaire to start on, as the Credo AI app picks it: with exactly one to
// choose from, that one, whatever was configured; otherwise a configured id
// from an older version is swapped for the current version with the same key.
function resolveQuestionnaireId(configured: string, available: QuestionnaireSummary[]): string {
  if (available.length === 1) {
    return available[0].id
  }
  if (!configured || available.some((q) => q.id === configured)) {
    return configured
  }
  const key = questionnaireBaseId(configured)
  return available.find((q) => q.key === key)?.id ?? configured
}

export function UseCaseSubmitForm({
  config,
  credo,
  platform,
  questionnaireId,
  appUrl,
  source,
  allowSubmit,
  hideCustomFields,
  disableQuestionnaireSelection,
  conditional,
  emit,
}: UseCaseSubmitFormProps) {
  const [available, setAvailable] = useState<QuestionnaireSummary[]>([])
  const [customFieldDefs, setCustomFieldDefs] = useState<CustomFieldDef[]>([])
  const [questionnaire, setQuestionnaire] = useState<Questionnaire | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [createdId, setCreatedId] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const methods = useForm<SubmitFormValues>({
    defaultValues: { ...EMPTY_VALUES, questionnaireId },
    mode: 'all',
  })
  const selectedId =
    useWatch({ name: 'questionnaireId', control: methods.control }) || questionnaireId

  useEffect(() => {
    let cancelled = false
    setIsLoading(true)
    void Promise.all([
      platform.listCreatableQuestionnaires('use_case'),
      credo.listCustomFieldDefs('use_case'),
    ])
      .then(([questionnaires, rawDefs]) => {
        if (cancelled) {
          return
        }
        setAvailable(questionnaires)
        setCustomFieldDefs(parseCustomFieldDefs(rawDefs))
        const resolvedId = resolveQuestionnaireId(questionnaireId, questionnaires)
        if (resolvedId !== questionnaireId) {
          methods.setValue('questionnaireId', resolvedId)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(errorMessage(err))
          emit('error', { message: errorMessage(err) })
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [credo, platform, questionnaireId, methods, emit])

  // Load the selected questionnaire's content, and reset any answers typed
  // against the previous one — they are keyed by its ids and mean nothing here.
  // Only a creatable questionnaire is loaded: a configured one that isn't
  // offered for creation renders no questions, as in the Credo AI app.
  const isSelectable = available.some((q) => q.id === selectedId)
  useEffect(() => {
    if (!isSelectable) {
      setQuestionnaire(null)
      return
    }
    let cancelled = false
    void credo
      .getQuestionnaire(selectedId)
      .then((doc) => {
        if (!cancelled) {
          setQuestionnaire(parseQuestionnaire(doc, { keepHidden: true }))
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(errorMessage(err))
        }
      })
    return () => {
      cancelled = true
    }
  }, [credo, selectedId, isSelectable])

  useEffect(() => {
    methods.resetField('questions')
  }, [selectedId, methods])

  const questionnaires = useMemo(
    () => (questionnaire ? [questionnaire] : []),
    [questionnaire],
  )
  const values = useWatch({ control: methods.control }) as Partial<UseCaseFormValues>
  const previews = useQuestionnairePreview(
    platform,
    questionnaire ? [questionnaire.id] : [],
    values,
    conditional,
  )

  // Host-page event surface, matching Credo AI's hosted web components.
  useEffect(() => {
    emit('form_values_changed', { ...parseUseCaseForm(values), questionnaireIds: [selectedId] })
  }, [values, selectedId, emit])

  const missing = useMemo(
    () => missingRequiredQuestions(values, questionnaires, previews),
    [values, questionnaires, previews],
  )
  const isValid = Boolean(values.name) && Boolean(selectedId) && missing.length === 0

  useEffect(() => {
    emit('form_state_changed', {
      isValid,
      isDirty: methods.formState.isDirty,
      missingRequired: missing,
    })
  }, [isValid, methods.formState.isDirty, missing, emit])

  // Reset returns to the questionnaire the form started on, keeping any
  // version swap made at load rather than reverting to the stale id.
  const resetQuestionnaireId = resolveQuestionnaireId(questionnaireId, available)

  const handleReset = () => {
    methods.reset({ ...EMPTY_VALUES, questionnaireId: resetQuestionnaireId })
    setCreatedId(null)
    setSubmitError(null)
  }

  const handleSubmit = () => {
    setIsSubmitting(true)
    setSubmitError(null)
    void credo
      .createUseCase({
        ...parseUseCaseForm(methods.getValues()),
        questionnaireIds: [selectedId],
        user: config.user_email ?? '',
        // Passed through for contract parity, but note: the platform currently
        // 422s on every non-empty value (see the README's limitations), so a
        // host that sets `source` today gets a failed submit rather than a
        // tagged use case.
        source: source || undefined,
      })
      .then((useCase) => {
        setCreatedId(useCase.id)
        emit('use_case_created', { useCaseId: useCase.id })
        methods.reset({ ...EMPTY_VALUES, questionnaireId: resetQuestionnaireId })
      })
      .catch((err) => {
        setSubmitError(errorMessage(err))
        emit('error', { message: errorMessage(err) })
      })
      .finally(() => setIsSubmitting(false))
  }

  if (isLoading) {
    return <ElementLoading label="Loading questionnaire…" />
  }

  const questionnaireLocked = disableQuestionnaireSelection && Boolean(questionnaireId)
  // Checked against the resolved selection rather than the configured id, so
  // a stale version that was swapped for the current one doesn't warn.
  const configuredQuestionnaireMissing =
    questionnaireLocked && available.length > 0 && !isSelectable

  return (
    <FormProvider {...methods}>
      <FormCard>
        <FormHeader
          title="Use case registration"
          subtitle="Provide all the required fields and submit the form to register a new use case."
        >
          <SecondaryButton onClick={handleReset}>Reset</SecondaryButton>
          {allowSubmit ? (
            <PrimaryButton disabled={!isValid || isSubmitting} onClick={handleSubmit}>
              {isSubmitting ? 'Submitting…' : 'Submit'}
            </PrimaryButton>
          ) : null}
        </FormHeader>

        {(loadError || submitError || createdId || configuredQuestionnaireMissing) && (
          <div className="flex flex-col gap-2 px-5 pt-4">
            {configuredQuestionnaireMissing ? (
              <FormBanner
                tone="warning"
                title="Invalid configuration"
                body="The configured questionnaire is not available for selection. Contact your administrator."
              />
            ) : null}
            {loadError ? <FormBanner tone="error" title="Could not load" body={loadError} /> : null}
            {submitError ? (
              <FormBanner tone="error" title="Submission failed" body={submitError} />
            ) : null}
            {createdId ? (
              <FormBanner
                tone="success"
                title="Use case registered"
                body={
                  appUrl
                    ? `View it at ${appUrl}/use-cases/${createdId}`
                    : 'Thank you for registering your use case.'
                }
              />
            ) : null}
          </div>
        )}

        <UseCaseInformationFields>
          <QuestionnairePicker questionnaires={available} locked={questionnaireLocked} />
        </UseCaseInformationFields>
        {hideCustomFields ? null : <UseCaseCustomFields defs={customFieldDefs} />}
        <UseCaseQuestions
          questionnaires={questionnaires}
          previews={conditional ? previews : undefined}
        />
        {questionnaires.length === 0 ? (
          <FormSection title="Questions">
            <p className="text-[13px] text-gray-500">
              Select a questionnaire to see its questions.
            </p>
          </FormSection>
        ) : null}
      </FormCard>
    </FormProvider>
  )
}
