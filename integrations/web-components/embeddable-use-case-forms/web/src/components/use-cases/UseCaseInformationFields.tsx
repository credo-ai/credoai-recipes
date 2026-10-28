import type { ReactNode } from 'react'
import { useController, useFormContext } from 'react-hook-form'

import { FormSection } from './FormLayout'

const inputClass =
  'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 ' +
  'focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500'

// Name + description, shared by all three forms. `children` is where the
// intake/create forms slot their questionnaire picker.
export function UseCaseInformationFields({ children }: { children?: ReactNode }) {
  const { control } = useFormContext()
  const name = useController({
    name: 'name',
    control,
    rules: { required: 'A name is required' },
  })
  const description = useController({ name: 'description', control })

  return (
    <FormSection title="Information">
      <div className="flex flex-col gap-4">
        <div>
          <label
            htmlFor="use-case-name"
            className="mb-1 block text-[13px] font-semibold text-gray-800"
          >
            Name <span className="text-red-600">*</span>
          </label>
          <input
            id="use-case-name"
            className={inputClass}
            value={(name.field.value as string) ?? ''}
            onChange={name.field.onChange}
            onBlur={name.field.onBlur}
            aria-invalid={Boolean(name.fieldState.error)}
          />
          {name.fieldState.error ? (
            <p className="mt-1 text-[12px] text-red-600">{name.fieldState.error.message}</p>
          ) : (
            <p className="mt-1 text-[12px] text-gray-500">This is the name of the use case.</p>
          )}
        </div>
        <div>
          <label
            htmlFor="use-case-description"
            className="mb-1 block text-[13px] font-semibold text-gray-800"
          >
            Description
          </label>
          <textarea
            id="use-case-description"
            rows={4}
            className={inputClass}
            value={(description.field.value as string) ?? ''}
            onChange={description.field.onChange}
            onBlur={description.field.onBlur}
          />
          <p className="mt-1 text-[12px] text-gray-500">
            Describe the context of the use case.
          </p>
        </div>
        {children}
      </div>
    </FormSection>
  )
}
