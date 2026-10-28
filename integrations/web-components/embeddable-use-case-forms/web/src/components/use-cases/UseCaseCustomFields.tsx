import type { CustomFieldDef } from '../../types'
import { customFieldName } from '../../lib/use-case-form'
import { CustomFieldControl } from '../CustomFieldControl'
import { FormSection } from './FormLayout'

// The tenant's use-case custom fields, rendered from their definitions.
// Hidden entirely when the host sets hide-custom-fields, or when the tenant
// has none.
export function UseCaseCustomFields({ defs }: { defs: CustomFieldDef[] }) {
  if (defs.length === 0) {
    return null
  }
  return (
    <FormSection title="Additional information">
      <div className="grid gap-4 sm:grid-cols-2">
        {defs.map((def) => (
          <CustomFieldControl key={def.id} def={def} name={customFieldName(def.id)} />
        ))}
      </div>
    </FormSection>
  )
}
