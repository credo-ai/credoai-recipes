import { useEffect, useMemo, useState } from 'react'
import { FormProvider, useForm, useWatch } from 'react-hook-form'

import { errorMessage } from '../../api/errors'
import type { CredoClient } from '../../api/credo'
import type { PlatformClient } from '../../api/platform'
import { parseQuestionnaire, parseUseCase, readCustomFieldValue } from '../../lib/compose'
import { parseCustomFieldDefs } from '../../lib/custom-fields'
import {
  type UseCaseFormValues,
  buildFormDefaults,
  missingRequiredQuestions,
  parseUseCaseForm,
} from '../../lib/use-case-form'
import type { CredoStatus, CustomFieldDef, Questionnaire } from '../../types'
import { ElementLoading } from '../ElementShell'
import {
  FormBanner,
  FormCard,
  FormHeader,
  PrimaryButton,
  SecondaryButton,
} from './FormLayout'
import { UseCaseCustomFields } from './UseCaseCustomFields'
import { UseCaseInformationFields } from './UseCaseInformationFields'
import { UseCaseQuestions } from './UseCaseQuestions'
import { useQuestionnairePreview } from './useQuestionnairePreview'

export type EditFormEvent =
  | 'use_case_updated'
  | 'form_values_changed'
  | 'form_state_changed'
  | 'error'

interface UseCaseEditFormProps {
  config: CredoStatus
  credo: CredoClient
  platform: PlatformClient
  useCaseId: string
  appUrl: string
  allowSubmit: boolean
  hideCustomFields: boolean
  emit: (event: EditFormEvent, detail: unknown) => void
}

interface EvidenceRecord {
  questionId: string
  value: unknown
}

// The evidence documents CredoClient returns are JSON:API-shaped (see
// credo.ts's evidenceToJsonApiDoc); this pulls out just the question->value
// pairs the form pre-fill needs.
function toEvidenceRecords(raw: unknown[]): EvidenceRecord[] {
  return raw.flatMap((item) => {
    const attributes = (item as { attributes?: Record<string, unknown> })?.attributes
    const questionId = (attributes?.label as { key?: string } | undefined)?.key
    const value = (attributes?.data as { value?: unknown } | undefined)?.value
    return questionId ? [{ questionId, value }] : []
  })
}

export function UseCaseEditForm({
  config,
  credo,
  platform,
  useCaseId,
  appUrl,
  allowSubmit,
  hideCustomFields,
  emit,
}: UseCaseEditFormProps) {
  const [questionnaires, setQuestionnaires] = useState<Questionnaire[]>([])
  const [customFieldDefs, setCustomFieldDefs] = useState<CustomFieldDef[]>([])
  const [defaults, setDefaults] = useState<UseCaseFormValues | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [isSaved, setIsSaved] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  const methods = useForm<UseCaseFormValues>({
    defaultValues: { name: '', description: '', customFields: {}, questions: {} },
    mode: 'all',
  })

  useEffect(() => {
    let cancelled = false
    setIsLoading(true)
    setLoadError(null)

    void (async () => {
      try {
        const [useCaseDoc, evidences, fieldValues, rawDefs] = await Promise.all([
          credo.getUseCase(useCaseId),
          credo.getUseCaseEvidences(useCaseId),
          credo.getUseCaseCustomFields(useCaseId),
          credo.listCustomFieldDefs('use_case'),
        ])
        const { useCase, questionnaireIds } = parseUseCase(useCaseDoc)
        const loaded: Questionnaire[] = []
        for (const id of questionnaireIds) {
          loaded.push(parseQuestionnaire(await credo.getQuestionnaire(id), { keepHidden: true }))
        }
        if (cancelled) {
          return
        }
        const defs = parseCustomFieldDefs(rawDefs)
        const nextDefaults = buildFormDefaults({
          name: useCase.name,
          description: useCase.description ?? '',
          questionnaires: loaded,
          evidences: toEvidenceRecords(evidences),
          customFieldValues: Object.fromEntries(
            defs.map((def) => [def.id, readCustomFieldValue(fieldValues, def.id) ?? '']),
          ),
        })
        setQuestionnaires(loaded)
        setCustomFieldDefs(defs)
        setDefaults(nextDefaults)
        methods.reset(nextDefaults)
      } catch (err) {
        if (!cancelled) {
          setLoadError(errorMessage(err))
          emit('error', { useCaseId, message: errorMessage(err) })
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [credo, useCaseId, methods, emit])

  const questionnaireIds = useMemo(() => questionnaires.map((q) => q.id), [questionnaires])
  const values = useWatch({ control: methods.control }) as Partial<UseCaseFormValues>
  const previews = useQuestionnairePreview(platform, questionnaireIds, values, true)

  useEffect(() => {
    emit('form_values_changed', { id: useCaseId, ...parseUseCaseForm(values) })
  }, [values, useCaseId, emit])

  const missing = useMemo(
    () => missingRequiredQuestions(values, questionnaires, previews),
    [values, questionnaires, previews],
  )
  const isValid = Boolean(values.name) && missing.length === 0

  useEffect(() => {
    emit('form_state_changed', {
      isValid,
      isDirty: methods.formState.isDirty,
      missingRequired: missing,
    })
  }, [isValid, methods.formState.isDirty, missing, emit])

  const handleReset = () => {
    if (defaults) {
      methods.reset(defaults)
    }
    setSaveError(null)
    setIsSaved(false)
  }

  const handleSubmit = () => {
    setIsSaving(true)
    setSaveError(null)
    setIsSaved(false)
    void credo
      .updateUseCaseFromForm(useCaseId, {
        ...parseUseCaseForm(methods.getValues()),
        user: config.user_email ?? '',
      })
      .then(() => {
        const saved = methods.getValues()
        setDefaults(saved)
        methods.reset(saved)
        setIsSaved(true)
        emit('use_case_updated', { useCaseId })
      })
      .catch((err) => {
        setSaveError(errorMessage(err))
        emit('error', { useCaseId, message: errorMessage(err) })
      })
      .finally(() => setIsSaving(false))
  }

  if (isLoading) {
    return <ElementLoading label="Loading use case…" />
  }

  return (
    <FormProvider {...methods}>
      <FormCard>
        <FormHeader
          title="Use case edit"
          subtitle="Complete all required fields before saving your changes."
        >
          {appUrl ? (
            <a
              href={`${appUrl}/use-cases/${useCaseId}`}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-[13px] font-semibold text-gray-700 hover:bg-gray-50"
            >
              Go to use case
            </a>
          ) : null}
          <SecondaryButton onClick={handleReset}>Reset</SecondaryButton>
          {allowSubmit ? (
            <PrimaryButton disabled={!isValid || isSaving} onClick={handleSubmit}>
              {isSaving ? 'Updating…' : 'Update'}
            </PrimaryButton>
          ) : null}
        </FormHeader>

        {(loadError || saveError || isSaved) && (
          <div className="flex flex-col gap-2 px-5 pt-4">
            {loadError ? <FormBanner tone="error" title="Could not load" body={loadError} /> : null}
            {saveError ? <FormBanner tone="error" title="Update failed" body={saveError} /> : null}
            {isSaved ? <FormBanner tone="success" title="Use case updated" body="" /> : null}
          </div>
        )}

        <UseCaseInformationFields />
        {hideCustomFields ? null : <UseCaseCustomFields defs={customFieldDefs} />}
        <UseCaseQuestions questionnaires={questionnaires} previews={previews} />
      </FormCard>
    </FormProvider>
  )
}
