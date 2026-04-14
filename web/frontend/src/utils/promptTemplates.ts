import { apiClient } from './apiClient'

export interface PromptTemplateBundle {
  generate_personality: {
    system_default: string
    user_default: string
  }
  generate_agent_type: {
    system_default: string
    user_default: string
  }
}

let promptTemplatesPromise: Promise<PromptTemplateBundle | null> | null = null

export function renderPromptTemplate(template: string, values: Record<string, string>): string {
  return template.replace(/\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}/g, (_, key: string) => values[key] ?? '')
}

export async function fetchPromptTemplates(): Promise<PromptTemplateBundle | null> {
  if (!promptTemplatesPromise) {
    promptTemplatesPromise = apiClient.get('/api/ai/prompt-templates')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || typeof data !== 'object') return null
        return data as PromptTemplateBundle
      })
      .catch(() => null)
  }
  return promptTemplatesPromise
}
