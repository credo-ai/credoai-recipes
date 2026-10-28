import { useMemo } from 'react'

import { CredoClient } from '../api/credo'
import { PlatformClient } from '../api/platform'
import { MissingTokenSourceError, resolveTokenSource } from '../api/tokens'
import { type UseCaseFormBaseProps, resolveFormConfig } from '../config'
import type { CredoStatus } from '../types'
import { attributeKey } from '../wc/attribute-key'

interface FormElementSetup {
  config: CredoStatus
  credo: CredoClient | null
  platform: PlatformClient | null
  error: string | null
}

// Shared wiring for the three use-case form elements: resolve attributes into
// config, build the two API clients, and surface the one failure a host page
// can actually cause (no usable credentials).
//
// Memoized on the attributes, not on props identity — see attributeKey.
export function useFormElementSetup(props: UseCaseFormBaseProps): FormElementSetup {
  const propsKey = attributeKey(props)

  return useMemo(() => {
    const config = resolveFormConfig(props)
    if (!config.tenant || !config.user_email) {
      return {
        config,
        credo: null,
        platform: null,
        error: 'Set the tenant and user attributes on this element.',
      }
    }
    try {
      const tokenSource = resolveTokenSource(props)
      return {
        config,
        credo: new CredoClient(config, tokenSource),
        platform: new PlatformClient(config, tokenSource),
        error: null,
      }
    } catch (err) {
      return {
        config,
        credo: null,
        platform: null,
        error: err instanceof MissingTokenSourceError ? err.message : String(err),
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propsKey])
}
