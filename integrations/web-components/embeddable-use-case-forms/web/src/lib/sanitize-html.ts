import DOMPurify from 'dompurify'

// The Preview footer is tenant configuration (previewFooterText), not
// end-user input, but this recipe is a reference a customer engineer copies
// into their own app — sanitize as defense-in-depth rather than trust it.
// Deliberately tighter than a general-purpose markdown sanitizer: a footer
// only ever needs inline text plus an optional link.
const ALLOWED_TAGS = ['b', 'strong', 'i', 'em', 'span', 'br', 'a']
const ALLOWED_ATTR = ['href', 'class']

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    // Force safe link attributes regardless of what was configured, to
    // prevent tab-nabbing via a tenant-authored link.
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export function sanitizeFooterHtml(raw: string): string {
  return DOMPurify.sanitize(raw, { ALLOWED_TAGS, ALLOWED_ATTR })
}
