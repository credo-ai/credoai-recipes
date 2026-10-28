import axios from 'axios'

import { CredoApiError } from './credo'

// What an element shows the end user when a load fails. Deliberately generic:
// the detailed errorMessage() goes to the console and the `error` event.
const ACCESS_ERROR_MESSAGE =
  'You may not have access to this use case, or your session has expired. Try reloading the page.'
export const GENERIC_ERROR_MESSAGE = 'Something went wrong on our side. Please try again in a moment.'

export function userFacingErrorMessage(err: unknown): string {
  if (err instanceof CredoApiError && (err.status === 401 || err.status === 403)) {
    return ACCESS_ERROR_MESSAGE
  }
  return GENERIC_ERROR_MESSAGE
}

// Detail for the console and the `error` host event.
export function errorMessage(err: unknown): string {
  if (err instanceof CredoApiError) {
    if (err.status === 401 || err.status === 403) {
      return (
        'Credo AI rejected the credentials — check CREDO_API_KEY and CREDO_TENANT ' +
        'in the token service .env'
      )
    }
    if (err.status === 404) {
      return 'Not found in Credo AI'
    }
    if ((err.status === 400 || err.status === 422) && err.body !== undefined) {
      return JSON.stringify(err.body)
    }
    return err.message
  }
  if (axios.isAxiosError(err)) {
    const detail: unknown = err.response?.data?.detail
    if (typeof detail === 'string') {
      return detail
    }
    if (detail !== undefined) {
      return JSON.stringify(detail)
    }
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}
