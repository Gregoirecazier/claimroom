export type VoiceTurn = { role: 'user' | 'assistant'; text: string; turn?: number }

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function conversationTurns(message: Record<string, unknown>): VoiceTurn[] {
  const parse = (raw: unknown): VoiceTurn[] => {
    if (!Array.isArray(raw)) return []
    return raw.flatMap(value => {
      const item = record(value)
      if (!item) return []
      const role = item.role === 'bot' || item.role === 'assistant' ? 'assistant'
        : item.role === 'user' ? 'user' : null
      const content = text(item.message ?? item.content)
      return role && content ? [{ role, text: content }] : []
    })
  }
  const spoken = parse(message.messages)
  return spoken.length ? spoken : parse(message.messagesOpenAIFormatted)
}

function withSpokenTurns(previous: VoiceTurn[], committed: VoiceTurn[]): VoiceTurn[] {
  const merged = [...committed]
  for (const [index, spoken] of previous.entries()) {
    if (spoken.role !== 'assistant' || spoken.turn === undefined ||
        merged.some(item => item.role === 'assistant' &&
          (item.text === spoken.text || item.text.startsWith(spoken.text) || spoken.text.startsWith(item.text)))) continue
    const precedingUser = previous.slice(0, index).reverse().find(item => item.role === 'user')
    const followingUser = previous.slice(index + 1).find(item => item.role === 'user')
    const previousIndex = precedingUser
      ? merged.reduce((found, item, itemIndex) => item.role === 'user' && item.text === precedingUser.text
        ? itemIndex : found, -1) : -1
    const nextIndex = followingUser
      ? merged.findIndex(item => item.role === 'user' && item.text === followingUser.text) : -1
    const insertion = previousIndex >= 0 ? previousIndex + 1
      : nextIndex >= 0 ? nextIndex : 0
    merged.splice(insertion, 0, spoken)
  }
  return merged
}

/** Vapi's committed conversation contains agent speech even when transcript events do not. */
export function updateVoiceTranscript(previous: VoiceTurn[], event: unknown): VoiceTurn[] {
  const message = record(event)
  if (!message) return previous
  if (message.type === 'conversation-update') {
    const committed = conversationTurns(message)
    return committed.length ? withSpokenTurns(previous, committed) : previous
  }
  if (message.type === 'assistant.speechStarted') {
    const spoken = text(message.text)
    if (!spoken) return previous
    const turn = typeof message.turn === 'number' ? message.turn : undefined
    const existing = turn === undefined ? -1 : previous.findIndex(item => item.role === 'assistant' && item.turn === turn)
    if (existing >= 0) return previous[existing].text === spoken ? previous
      : previous.map((item, index) => index === existing ? { role: 'assistant', text: spoken, turn } : item)
    const last = previous.at(-1)
    if (last?.role === 'assistant' && (turn !== undefined && last.turn === turn || last.text === spoken)) {
      return last.text === spoken && last.turn === turn ? previous
        : [...previous.slice(0, -1), { role: 'assistant', text: spoken, turn }]
    }
    return [...previous, { role: 'assistant', text: spoken, turn }]
  }
  if (message.type !== 'transcript' && message.type !== "transcript[transcriptType='final']") return previous
  if (message.transcriptType !== 'final' || (message.role !== 'assistant' && message.role !== 'user')) return previous
  const finalText = text(message.transcript)
  if (!finalText) return previous
  const last = previous.at(-1)
  return last?.role === message.role && last.text === finalText
    ? previous : [...previous, { role: message.role, text: finalText }]
}
