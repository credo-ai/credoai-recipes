// A stable identity for an element's attributes.
//
// r2wc re-renders with a fresh props object each time, so memoizing on props
// identity rebuilds API clients (and discards their cached tokens) on every
// render. It also passes non-attribute props — notably `container`, the shadow
// root, which is CIRCULAR — so the whole props object cannot be serialized.
//
// Only primitive-valued props correspond to attributes, so those are the only
// ones that belong in the key.
export function attributeKey(props: object): string {
  const attributes = Object.entries(props)
    .filter(([, value]) => value === null || ['string', 'number', 'boolean'].includes(typeof value))
    .sort(([a], [b]) => a.localeCompare(b))
  return JSON.stringify(attributes)
}
