import { describe, expect, it } from 'vitest'

import {
  controlTypeForCustomField,
  customFieldFormKey,
  customFieldIdFromFormKey,
  isBooleanCustomField,
  isCustomFieldFormKey,
  parseCustomFieldDefs,
  resolveConfiguredCustomFields,
} from './custom-fields'

describe('controlTypeForCustomField', () => {
  it('maps every confirmed element_type', () => {
    expect(controlTypeForCustomField({ id: '1', name: '', element_type: 'select', options: [], multiple: false })).toBe('select')
    expect(controlTypeForCustomField({ id: '1', name: '', element_type: 'checkbox', options: [], multiple: false })).toBe('checkbox')
    expect(controlTypeForCustomField({ id: '1', name: '', element_type: 'datepicker', options: [], multiple: false })).toBe('date')
    expect(controlTypeForCustomField({ id: '1', name: '', element_type: 'input', options: [], multiple: false })).toBe('text')
  })

  it('falls through to text for an unrecognized element_type', () => {
    expect(controlTypeForCustomField({ id: '1', name: '', element_type: 'number', options: [], multiple: false })).toBe('text')
  })
})

describe('isBooleanCustomField', () => {
  it('is true only for checkbox fields', () => {
    expect(isBooleanCustomField({ def: { id: '1', name: '', element_type: 'checkbox', options: [], multiple: false }, value: true })).toBe(true)
    expect(isBooleanCustomField({ def: { id: '1', name: '', element_type: 'select', options: [], multiple: false }, value: 'x' })).toBe(false)
  })
})

describe('form key helpers', () => {
  it('round-trips a field id through the form key', () => {
    expect(customFieldFormKey('abc')).toBe('cf:abc')
    expect(isCustomFieldFormKey('cf:abc')).toBe(true)
    expect(isCustomFieldFormKey('q-product')).toBe(false)
    expect(customFieldIdFromFormKey('cf:abc')).toBe('abc')
  })
})

describe('parseCustomFieldDefs', () => {
  it('parses id/name/element_type/options/multiple from the wire shape', () => {
    const defs = parseCustomFieldDefs([
      {
        id: 'cf-1',
        attributes: { name: 'Assessment Plan', element_type: 'select', options: ['Lite', 'Full'], multiple: false },
      },
    ])
    expect(defs).toEqual([
      { id: 'cf-1', name: 'Assessment Plan', element_type: 'select', options: ['Lite', 'Full'], multiple: false },
    ])
  })
})

describe('resolveConfiguredCustomFields', () => {
  const defs = parseCustomFieldDefs([
    { id: 'cf-1', attributes: { name: 'Business Owner', element_type: 'input', options: [], multiple: false } },
    { id: 'cf-2', attributes: { name: 'Deployment Region', element_type: 'select', options: ['US', 'EU'], multiple: false } },
  ])
  const values = [
    { attributes: { custom_field_id: 'cf-1', value: 'Jane Doe' } },
  ]

  it('resolves configured names to defs and current values, defaulting to null when unanswered', () => {
    const entries = resolveConfiguredCustomFields(defs, values, ['Business Owner', 'Deployment Region'])
    expect(entries).toEqual([
      { def: defs[0], value: 'Jane Doe' },
      { def: defs[1], value: null },
    ])
  })

  it('silently omits a configured name that matches no real field', () => {
    const entries = resolveConfiguredCustomFields(defs, values, ['Business Owner', 'Nope'])
    expect(entries.map((e) => e.def.name)).toEqual(['Business Owner'])
  })

  it('reads a value via the custom_field relationship shape', () => {
    const relValues = [
      { attributes: { value: 'Jane Doe' }, relationships: { custom_field: { data: { id: 'cf-1' } } } },
    ]
    const entries = resolveConfiguredCustomFields(defs, relValues, ['Business Owner'])
    expect(entries[0].value).toBe('Jane Doe')
  })
})
