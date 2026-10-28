import { useRef, useState } from 'react'
import { useController, useFormContext } from 'react-hook-form'

import { isBlankAnswer } from '../lib/blocking-questionnaires'
import type { DiffState } from '../lib/diff'
import { controlTypeFor, isEmptyValue, validationRulesFor } from '../lib/render-rules'
import type { CardFormValues, Question } from '../types'
import { DiffAnnotation } from './DiffAnnotation'

interface QuestionFieldProps {
  question: Question
  // react-hook-form field path. Defaults to the bare question id (the flat
  // shape the transparency card uses); the use-case forms pass the nested
  // `questions.<questionnaire>.<section>.<question>` path instead.
  name?: string
  // True when this is the question statusQuestionId resolves to —
  // an unconditional marker independent of the field's current value
  // (contrast with the Preview hero badge, which is value-conditional).
  isBadge?: boolean
  // True when an empty value blocks the card's Save (blocking-questionnaires):
  // the advisory amber "Required" becomes a red error. Only ever escalates a
  // question that is already required.
  blocking?: boolean
  // Pending refresh-from-upstream diff for this question, if any — the
  // control below stays live/editable but tints amber, with the upstream
  // value and Accept/Reject shown underneath it.
  diff?: DiffState
  onAcceptDiff?: () => void
  onRejectDiff?: () => void
}

// A number question's saved answer can come back from the API as a number.
function toStringValue(value: unknown): string {
  if (typeof value === 'number') {
    return String(value)
  }
  return typeof value === 'string' ? value : ''
}

function toArrayValue(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === 'string')
    : []
}

// Multi-select with 6+ options: selected values render as removable chips
// inside an input-like container, with a filterable dropdown of the remaining
// options. Mirrors the platform's Autocomplete isMultiple control.
function ChipSelect({
  options,
  values,
  onChange,
  onBlur,
  isDiffPending,
}: {
  options: string[]
  values: string[]
  onChange: (next: string[]) => void
  onBlur: () => void
  isDiffPending?: boolean
}) {
  const [query, setQuery] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const available = options.filter(
    (option) =>
      !values.includes(option) &&
      option.toLowerCase().includes(query.toLowerCase()),
  )

  return (
    <div className="relative">
      <div
        className={
          'flex w-full cursor-text flex-wrap items-center gap-1.5 rounded-lg border ' +
          (isDiffPending ? 'border-[#ffc333] bg-[#fffdf6] ' : 'border-gray-300 ') +
          'px-2 py-1.5 focus-within:border-purple-500 ' +
          'focus-within:ring-1 focus-within:ring-purple-500'
        }
        onClick={() => inputRef.current?.focus()}
      >
        {values.map((option) => (
          <span
            key={option}
            className="flex items-center gap-1 rounded bg-gray-100 px-1.5 py-0.5 text-sm text-gray-800"
          >
            {option}
            <button
              type="button"
              aria-label={`deselect ${option}`}
              className="text-gray-500 hover:text-gray-800"
              onClick={() => onChange(values.filter((v) => v !== option))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          className="min-w-[80px] flex-1 py-0.5 text-sm text-gray-900 focus:outline-none"
          placeholder={values.length === 0 ? 'Select…' : ''}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setIsOpen(true)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Backspace' && query === '' && values.length > 0) {
              onChange(values.slice(0, -1))
            }
          }}
          onFocus={() => setIsOpen(true)}
          onBlur={() => {
            setIsOpen(false)
            setQuery('')
            onBlur()
          }}
        />
      </div>
      {isOpen && (
        <ul className="absolute z-10 mt-1 max-h-48 w-full overflow-y-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg">
          {available.length === 0 ? (
            <li className="px-3 py-1.5 text-sm text-gray-500">No options</li>
          ) : (
            available.map((option) => (
              <li key={option}>
                <button
                  type="button"
                  className="w-full px-3 py-1.5 text-left text-sm text-gray-800 hover:bg-gray-50"
                  // Prevent the input's blur from closing the list before click fires.
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    onChange([...values, option])
                    setQuery('')
                    inputRef.current?.focus()
                  }}
                >
                  {option}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}

export function QuestionField({
  question,
  name,
  isBadge,
  blocking,
  diff,
  onAcceptDiff,
  onRejectDiff,
}: QuestionFieldProps) {
  const { control } = useFormContext<CardFormValues>()
  const {
    field,
    fieldState: { error, isTouched, isDirty },
  } = useController({
    name: name ?? question.id,
    control,
    rules: validationRulesFor(question),
  })

  const controlType = controlTypeFor(question)
  const inputClass =
    'w-full rounded-lg border px-3 py-2 text-sm text-gray-900 ' +
    (diff ? 'border-[#ffc333] bg-[#fffdf6] ' : 'border-gray-300 ') +
    'focus:border-purple-500 focus:outline-none focus:ring-1 focus:ring-purple-500'
  const diffBoxClass = diff ? 'rounded-lg border border-[#ffc333] bg-[#fffdf6] p-2' : ''

  const isEmpty = blocking ? isBlankAnswer(field.value) : isEmptyValue(field.value)
  // Advisory (amber) by default — the transparency card only writes
  // evidences, it isn't the questionnaire submission flow, so a required
  // question left empty doesn't block Save unless its questionnaire is listed
  // in blocking-questionnaires (red, `blocking`). It surfaces only after the user
  // has actually interacted with the field: isTouched alone misses
  // checkbox/radio (group-type) controls, which set isDirty on change but
  // don't reliably set isTouched the way text inputs do.
  const hasInteracted = isTouched || isDirty
  const showRequiredMessage = question.required && isEmpty && hasInteracted
  const showBlockingError = showRequiredMessage && Boolean(blocking)
  const showRequiredWarning = showRequiredMessage && !blocking
  const fieldKey = name ?? question.id
  const messageId = showBlockingError ? `${fieldKey}-error` : `${fieldKey}-warning`
  // Spread onto the control (or the option group). Only the blocking error is
  // aria-invalid; the amber warning is advisory.
  const a11yProps = showBlockingError
    ? { 'aria-invalid': true as const, 'aria-describedby': messageId }
    : showRequiredWarning
      ? { 'aria-describedby': messageId }
      : {}

  const toggleOption = (option: string, checked: boolean) => {
    const current = toArrayValue(field.value)
    field.onChange(
      checked ? [...current, option] : current.filter((v) => v !== option),
    )
  }

  const renderControl = () => {
    switch (controlType) {
      case 'textarea':
        return (
          <textarea
            {...a11yProps}
            rows={4}
            className={inputClass}
            value={toStringValue(field.value)}
            onChange={(e) => field.onChange(e.target.value)}
            onBlur={field.onBlur}
          />
        )
      case 'input':
      case 'number':
      case 'date':
        return (
          <input
            {...a11yProps}
            type={controlType === 'input' ? 'text' : controlType}
            className={inputClass}
            value={toStringValue(field.value)}
            onChange={(e) => field.onChange(e.target.value)}
            onBlur={field.onBlur}
          />
        )
      case 'radio':
        return (
          <div role="radiogroup" {...a11yProps} className={diffBoxClass}>
            <div className="flex flex-col gap-1.5">
              {question.select_options.map((option) => (
                <label key={option} className="flex items-center gap-2 text-sm text-gray-800">
                  <input
                    type="radio"
                    name={question.id}
                    className="accent-purple-700"
                    checked={field.value === option}
                    onChange={() => field.onChange(option)}
                  />
                  {option}
                </label>
              ))}
            </div>
          </div>
        )
      case 'select':
        return (
          <select
            {...a11yProps}
            className={`${inputClass} select-chevron pr-10`}
            value={toStringValue(field.value)}
            onChange={(e) => field.onChange(e.target.value)}
            onBlur={field.onBlur}
          >
            <option value="">Select…</option>
            {question.select_options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        )
      case 'checkbox-group': {
        const values = toArrayValue(field.value)
        return (
          <div role="group" {...a11yProps} className={diffBoxClass}>
            <div className="flex flex-col gap-1.5">
              {question.select_options.map((option) => (
                <label key={option} className="flex items-center gap-2 text-sm text-gray-800">
                  <input
                    type="checkbox"
                    className="accent-purple-700"
                    checked={values.includes(option)}
                    onChange={(e) => toggleOption(option, e.target.checked)}
                  />
                  {option}
                </label>
              ))}
            </div>
          </div>
        )
      }
      case 'chip-select':
        return (
          <ChipSelect
            options={question.select_options}
            values={toArrayValue(field.value)}
            onChange={field.onChange}
            onBlur={field.onBlur}
            isDiffPending={Boolean(diff)}
          />
        )
      case 'unsupported':
        return (
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-500">
            {isEmptyValue(field.value)
              ? `This field type (${question.evidence_type}) isn't editable in this form.`
              : `${String(field.value)} — this field type (${question.evidence_type}) isn't editable in this form.`}
          </div>
        )
    }
  }

  return (
    <div className="py-3">
      <label className="mb-1.5 flex items-center gap-2 text-sm font-semibold text-gray-900">
        {question.text}
        {question.required && <span className="text-purple-700">*</span>}
        {isBadge && (
          <span className="rounded-full bg-purple-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-purple-800">
            Customer badge
          </span>
        )}
      </label>
      {question.description && (
        <p className="mb-1.5 text-xs text-gray-500">{question.description}</p>
      )}
      <div
        className={
          showBlockingError
            ? 'rounded-lg ring-1 ring-red-500'
            : showRequiredWarning
              ? 'rounded-lg ring-1 ring-amber-400'
              : undefined
        }
      >
        {renderControl()}
      </div>
      {error && <p className="mt-1 text-xs text-red-600">{error.message}</p>}
      {!error && showRequiredMessage && (
        <p
          id={messageId}
          className={`mt-1 text-xs ${showBlockingError ? 'text-red-600' : 'text-amber-600'}`}
        >
          {question.text} is required
        </p>
      )}
      {diff && onAcceptDiff && onRejectDiff && (
        <DiffAnnotation diff={diff} onAccept={onAcceptDiff} onReject={onRejectDiff} />
      )}
    </div>
  )
}
