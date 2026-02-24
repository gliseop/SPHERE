/**
 * Extract a day key from an event for grouping.
 * Returns ISO date string (YYYY-MM-DD) or `round-{N}` for old format.
 */
export function toDayKey(event: { timestamp?: string; round?: number | null }): string {
  if (event.timestamp) {
    try {
      // Prefer the original timestamp's date component to avoid UTC shifting.
      const m = /^(\d{4}-\d{2}-\d{2})/.exec(event.timestamp)
      if (m?.[1]) return m[1]

      // Fallback for non-ISO timestamps: derive local YYYY-MM-DD.
      const d = new Date(event.timestamp)
      if (!isNaN(d.getTime())) {
        const y = d.getFullYear()
        const mm = String(d.getMonth() + 1).padStart(2, '0')
        const dd = String(d.getDate()).padStart(2, '0')
        return `${y}-${mm}-${dd}`
      }
    } catch { /* fall through */ }
  }
  if (typeof event.round === 'number') {
    return `round-${event.round}`
  }
  return 'undated'
}

/**
 * Format a day key for display.
 */
export function formatDayLabel(dayKey: string): string {
  if (dayKey.startsWith('round-')) {
    return `Раунд ${dayKey.slice(6)}`
  }
  if (dayKey === 'undated') {
    return 'Без даты'
  }
  try {
    const d = new Date(dayKey + 'T12:00:00')
    const weekday = d.toLocaleDateString('ru-RU', { weekday: 'long' })
    const date = d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
    return `${weekday}, ${date}`
  } catch {
    return dayKey
  }
}

/**
 * Format time from ISO timestamp (HH:MM).
 */
export function formatTime(timestamp: string): string {
  try {
    const d = new Date(timestamp)
    if (!isNaN(d.getTime())) {
      return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    }
  } catch { /* fall through */ }
  return ''
}
