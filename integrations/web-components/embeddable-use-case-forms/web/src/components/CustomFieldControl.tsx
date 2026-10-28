import { useController, useFormContext } from 'react-hook-form'

import { controlTypeForCustomField, customFieldFormKey } from '../lib/custom-fields'
import type { DiffState } from '../lib/diff'
import type { CardFormValues, CustomFieldDef } from '../types'
import { DiffAnnotation } from './DiffAnnotation'

interface CustomFieldControlProps {
  def: CustomFieldDef
  // react-hook-form field path; defaults to the transparency card's `cf:<id>`
  // key. The use-case forms pass `customFields.<id>` instead.
  name?: string
  // Pending refresh-from-upstream diff for this field, if any — see
  // QuestionField for the same treatment (live control tinted amber, plus
  // an Upstream caption and Accept/Reject below it). Boolean (checkbox)
  // custom fields never carry a diff (see isBooleanCustomField).
  diff?: DiffState
  onAcceptDiff?: () => void
  onRejectDiff?: () => void
}

function toArrayValue(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

export function CustomFieldControl({
  def,
  name,
  diff,
  onAcceptDiff,
  onRejectDiff,
}: CustomFieldControlProps) {
  const { control } = useFormContext<CardFormValues>()
  const { field } = useController({ name: name ?? customFieldFormKey(def.id), control })
  const controlType = controlTypeForCustomField(def)

  const inputClass =
    'w-full rounded-lg border px-3 py-2 text-sm text-gray-900 ' +
    (diff ? 'border-[#ffc333] bg-[#fffdf6] ' : 'border-gray-300 ') +
    'focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500'
  const diffBoxClass = diff ? 'rounded-lg border border-[#ffc333] bg-[#fffdf6] p-2' : ''

  const renderControl = () => {
    if (controlType === 'checkbox') {
      return (
        <label className="flex items-center gap-2 text-sm text-gray-800">
          <input
            type="checkbox"
            className="accent-purple-700"
            checked={Boolean(field.value)}
            onChange={(e) => field.onChange(e.target.checked)}
          />
          Yes
        </label>
      )
    }
    if (controlType === 'select' && def.multiple) {
      const values = toArrayValue(field.value)
      return (
        <div className={diffBoxClass}>
          <div className="flex flex-col gap-1.5">
            {def.options.map((option) => (
              <label key={option} className="flex items-center gap-2 text-sm text-gray-800">
                <input
                  type="checkbox"
                  className="accent-purple-700"
                  checked={values.includes(option)}
                  onChange={(e) =>
                    field.onChange(
                      e.target.checked ? [...values, option] : values.filter((v) => v !== option),
                    )
                  }
                />
                {option}
              </label>
            ))}
          </div>
        </div>
      )
    }
    if (controlType === 'select') {
      return (
        <select
          className={`${inputClass} select-chevron pr-10`}
          value={typeof field.value === 'string' ? field.value : ''}
          onChange={(e) => field.onChange(e.target.value)}
          onBlur={field.onBlur}
        >
          <option value="">Select…</option>
          {def.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      )
    }
    return (
      <input
        type={controlType === 'date' ? 'date' : 'text'}
        className={inputClass}
        value={typeof field.value === 'string' ? field.value : ''}
        onChange={(e) => field.onChange(e.target.value)}
        onBlur={field.onBlur}
      />
    )
  }

  return (
    <div className="py-3">
      <label className="mb-1.5 block text-sm font-semibold text-gray-900">{def.name}</label>
      {renderControl()}
      {diff && onAcceptDiff && onRejectDiff && (
        <DiffAnnotation diff={diff} onAccept={onAcceptDiff} onReject={onRejectDiff} />
      )}
    </div>
  )
}
