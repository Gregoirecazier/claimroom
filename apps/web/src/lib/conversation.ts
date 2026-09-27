export type ConversationMessage = {
  id: number
  side: 'agent' | 'you'
  text: string
  createdAt: string
  evidenceId?: string
}

const key = (caseId: string) => `claimroom:conversation:${caseId}`

export function readConversation(caseId: string): ConversationMessage[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key(caseId)) || '[]')
    return Array.isArray(saved) ? saved.filter(item => item && typeof item.id === 'number' &&
      (item.side === 'agent' || item.side === 'you') && typeof item.text === 'string' &&
      typeof item.createdAt === 'string') : []
  } catch { return [] }
}

export function saveConversation(caseId: string, messages: ConversationMessage[]) {
  try {
    sessionStorage.setItem(key(caseId), JSON.stringify(messages))
    window.dispatchEvent(new CustomEvent('claimroom:conversation-updated', { detail: caseId }))
  } catch { /* The chat still works when browser storage is unavailable. */ }
}
