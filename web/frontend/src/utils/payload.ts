/**
 * Type-safe helpers for accessing fields in Record<string, unknown> payloads.
 */

export function getString(obj: Record<string, unknown>, key: string): string {
  const v = obj[key]
  return typeof v === 'string' ? v : ''
}

export function getNumber(obj: Record<string, unknown>, key: string): number | null {
  const v = obj[key]
  return typeof v === 'number' ? v : null
}

export function getBool(obj: Record<string, unknown>, key: string): boolean {
  const v = obj[key]
  return v === true
}
