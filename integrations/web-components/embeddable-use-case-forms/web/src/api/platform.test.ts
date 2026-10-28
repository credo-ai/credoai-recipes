import { describe, expect, it } from 'vitest'

import { parseCreatableQuestionnaires } from './platform'

// Shapes taken from a live probe of the platform API: the SDK list's
// `isPublished` does not reflect creation-time availability, so the picker
// filters on these two metadata flags instead.
function entry(
  id: string,
  name: string,
  metadata: Record<string, unknown> | undefined,
  version: unknown = 1,
  key: unknown = `${id}-key`,
) {
  return { id, attributes: { key, name, version, metadata } }
}

describe('parseCreatableQuestionnaires', () => {
  it('keeps questionnaires flagged to show on create and not archived', () => {
    const result = parseCreatableQuestionnaires([
      entry('q1', 'Preliminary Review', { archived: false, show_on_create: true }),
    ])
    expect(result).toEqual([{ id: 'q1', key: 'q1-key', name: 'Preliminary Review', version: 1 }])
  })

  it('drops archived questionnaires', () => {
    expect(
      parseCreatableQuestionnaires([
        entry('q1', 'Old', { archived: true, show_on_create: true }),
      ]),
    ).toEqual([])
  })

  it('treats a missing show_on_create as not an opt-in', () => {
    expect(parseCreatableQuestionnaires([entry('q1', 'No flag', { archived: false })])).toEqual([])
    expect(parseCreatableQuestionnaires([entry('q1', 'No metadata', undefined)])).toEqual([])
  })

  it('sorts by name', () => {
    const meta = { archived: false, show_on_create: true }
    const result = parseCreatableQuestionnaires([
      entry('q1', 'Zebra', meta),
      entry('q2', 'Alpha', meta),
    ])
    expect(result.map((q) => q.name)).toEqual(['Alpha', 'Zebra'])
  })

  it('skips malformed rows rather than surfacing partial entries', () => {
    const meta = { archived: false, show_on_create: true }
    const result = parseCreatableQuestionnaires([
      { id: 42, attributes: { name: 'Bad id', metadata: meta } },
      { attributes: { name: 'No id', metadata: meta } },
      entry('q1', 'Good', meta, 'not-a-number', 7),
    ])
    expect(result).toEqual([{ id: 'q1', key: null, name: 'Good', version: null }])
  })
})
