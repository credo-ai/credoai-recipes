import { useController, useFormContext } from 'react-hook-form'

import type { QuestionnaireSummary } from '../../types'

// The intake/create forms' questionnaire selector. Locked to the configured
// questionnaire when the host sets disable-questionnaire-selection, otherwise
// (the default) the submitter picks from the tenant's active list.
export function QuestionnairePicker({
  questionnaires,
  locked,
}: {
  questionnaires: QuestionnaireSummary[]
  locked: boolean
}) {
  const { control } = useFormContext()
  const { field, fieldState } = useController({
    name: 'questionnaireId',
    control,
    rules: { required: 'Select a questionnaire' },
  })

  return (
    <div>
      <label
        htmlFor="questionnaire-id"
        className="mb-1 block text-[13px] font-semibold text-gray-800"
      >
        Questionnaire <span className="text-red-600">*</span>
      </label>
      <select
        id="questionnaire-id"
        disabled={locked}
        className="select-chevron w-full rounded-lg border border-gray-300 px-3 py-2 pr-10 text-sm text-gray-900 focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500 disabled:bg-gray-50 disabled:text-gray-500"
        value={(field.value as string) ?? ''}
        onChange={field.onChange}
        onBlur={field.onBlur}
      >
        <option value="">Select a questionnaire</option>
        {questionnaires.map((questionnaire) => (
          <option key={questionnaire.id} value={questionnaire.id}>
            {questionnaire.name}
          </option>
        ))}
      </select>
      {fieldState.error ? (
        <p className="mt-1 text-[12px] text-red-600">{fieldState.error.message}</p>
      ) : (
        <p className="mt-1 text-[12px] text-gray-500">
          {locked
            ? 'This questionnaire is preselected and cannot be changed.'
            : 'Select the questionnaire that best fits the use case.'}
        </p>
      )}
    </div>
  )
}
