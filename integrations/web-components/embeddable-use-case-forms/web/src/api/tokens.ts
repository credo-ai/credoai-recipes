import axios from 'axios'

import type { CredoTokens } from '../types'

// Where an element gets its short-lived Credo AI token. Two supported
// shapes, both of which keep the API key out of the browser:
//
// - `tokenEndpoint`: a host-owned endpoint that mints the token on demand
//   (this recipe's `server/python/` is one). Refreshable, so a long-lived page keeps
//   working after the first token expires.
// - a static token: the host page exchanged its API key server-side and passed
//   the result in as the `integration-token` attribute, per the Credo AI
//   integration guide. Not
//   refreshable — when it expires the host must set a new attribute.
export type TokenSource = () => Promise<CredoTokens>

export function endpointTokenSource(endpoint: string): TokenSource {
  return async () => {
    const { data } = await axios.post<CredoTokens>(endpoint)
    return data
  }
}

export function staticTokenSource(integrationToken: string): TokenSource {
  const tokens: CredoTokens = { integration_api_token: integrationToken }
  return () => Promise.resolve(tokens)
}

export class MissingTokenSourceError extends Error {
  constructor() {
    super(
      'No credentials: set token-endpoint or integration-token.',
    )
    this.name = 'MissingTokenSourceError'
  }
}

export function resolveTokenSource(props: {
  tokenEndpoint?: string
  integrationToken?: string
}): TokenSource {
  if (props.tokenEndpoint) {
    return endpointTokenSource(props.tokenEndpoint)
  }
  if (props.integrationToken) {
    return staticTokenSource(props.integrationToken)
  }
  throw new MissingTokenSourceError()
}
