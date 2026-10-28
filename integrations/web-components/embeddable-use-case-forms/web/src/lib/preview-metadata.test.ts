import { describe, expect, it, vi } from 'vitest'

import { buildMetadataRows } from './preview-metadata'

const attributes = {
  name: 'Support Copilot',
  description: '',
  risk_classification_level: 2,
  domain_ids: null,
  industries: [],
  settings: { use_risk_evaluations: true },
}

describe('buildMetadataRows', () => {
  it('warns and skips a name with no matching property', () => {
    const warn = vi.fn()
    const rows = buildMetadataRows(attributes, ['not_a_real_field'], warn)
    expect(rows).toEqual([])
    expect(warn).toHaveBeenCalledOnce()
  })

  it('renders a null/blank existing property as a placeholder with no warning', () => {
    const warn = vi.fn()
    const rows = buildMetadataRows(attributes, ['description', 'domain_ids'], warn)
    expect(rows).toEqual([
      { key: 'description', label: 'Description', value: null },
      { key: 'domain_ids', label: 'Domain Ids', value: null },
    ])
    expect(warn).not.toHaveBeenCalled()
  })

  it('silently skips a list/object-valued property', () => {
    const warn = vi.fn()
    const rows = buildMetadataRows(attributes, ['industries', 'settings'], warn)
    expect(rows).toEqual([])
    expect(warn).not.toHaveBeenCalled()
  })

  it('formats snake_case labels and stringifies non-blank values', () => {
    const rows = buildMetadataRows(attributes, ['risk_classification_level'])
    expect(rows).toEqual([
      { key: 'risk_classification_level', label: 'Risk Classification Level', value: '2' },
    ])
  })

  it('dedupes requests that differ only by case', () => {
    const rows = buildMetadataRows(attributes, ['name', 'Name', 'NAME'])
    expect(rows).toHaveLength(1)
    expect(rows[0].key).toBe('name')
  })
})
