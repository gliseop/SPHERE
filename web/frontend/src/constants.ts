/** Порог силы связи, выше которого ребро считается подозрительным. */
export const SUSPICIOUS_THRESHOLD = 3.0

/** Название приложения (для ребрендинга через VITE_APP_NAME). */
export const APP_NAME = import.meta.env.VITE_APP_NAME || 'SPHERE'

function agentSlug(id: string): string {
  return id.startsWith('agent:') ? id.slice('agent:'.length) : id
}

export function isGovernanceAgentId(id: string): boolean {
  const slug = agentSlug(id)
  return slug === 'auditor' || slug.startsWith('aud_') || slug.startsWith('juror_')
}

export function agentRoleLabel(id: string): string {
  const slug = agentSlug(id)
  if (slug.startsWith('off_')) return 'Чиновник'
  if (slug.startsWith('biz_')) return 'Подрядчик'
  if (slug === 'auditor' || slug.startsWith('aud_')) return 'Аудитор'
  if (slug.startsWith('juror_')) return 'Присяжный'
  if (slug.startsWith('fam_')) return 'Семья'
  if (slug.startsWith('soc_')) return 'Общество'
  return 'Агент'
}

export function agentRoleClass(id: string): string {
  const slug = agentSlug(id)
  if (slug.startsWith('off_')) return 'danger'
  if (slug.startsWith('biz_')) return 'info'
  if (slug === 'auditor' || slug.startsWith('aud_')) return 'accent'
  if (slug.startsWith('juror_')) return 'violet'
  if (slug.startsWith('fam_')) return 'warning'
  if (slug.startsWith('soc_')) return 'success'
  return ''
}

export function agentRoleBadgeClass(id: string): string {
  const roleClass = agentRoleClass(id)
  return roleClass ? `badge ${roleClass}` : 'badge'
}

export function agentRoleColor(id: string): string {
  const slug = agentSlug(id)
  if (slug.startsWith('off_')) return '#ef4444'
  if (slug.startsWith('biz_')) return '#3b82f6'
  if (slug === 'auditor' || slug.startsWith('aud_')) return '#f97316'
  if (slug.startsWith('juror_')) return '#a78bfa'
  if (slug.startsWith('fam_')) return '#f59e0b'
  if (slug.startsWith('soc_')) return '#10b981'
  return '#6b7280'
}
