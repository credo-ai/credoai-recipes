import { afterEach, describe, expect, it, vi } from 'vitest'

import { parseHideHeaderText, resolveCardConfig } from './config'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('parseHideHeaderText', () => {
  it.each(['', 'true', 'TRUE', ' 1 ', 'yes'])('hides for %j', (raw) => {
    expect(parseHideHeaderText(raw)).toBe(true)
  })

  it.each([undefined, 'false', '0', 'no'])('shows for %j without warning', (raw) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseHideHeaderText(raw)).toBe(false)
    expect(warn).not.toHaveBeenCalled()
  })

  it('fails closed to shown, with a warning, for anything else', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(parseHideHeaderText('hidden')).toBe(false)
    expect(warn).toHaveBeenCalledOnce()
  })
})

describe('resolveCardConfig', () => {
  it('passes the new attributes through', () => {
    const config = resolveCardConfig({ blockingQuestionnaires: '["TCV2"]', hideHeaderText: 'true' })
    expect(config.blocking_questionnaires).toBe('["TCV2"]')
    expect(config.hide_header_text).toBe(true)
  })
})
