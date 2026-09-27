import { guestRequest } from './depositTransport'

export type ChatProposal = { field: string; value: string }
export type AssistantMessage = { id: string; role: 'insured' | 'assistant'; text: string; created_at: string }
export type Phase = 'confirm' | 'correct' | 'collect' | 'photos' | 'complete'
export type ChatHistoryState = {
  phase: Phase; opening: string; openingAt: string; messages: AssistantMessage[]; proposal: ChatProposal | null
}
export type ChatHistory = { revision: number; state: ChatHistoryState | null }

// Serialize checkpoints so a slower request cannot overwrite a newer turn.
// Keep the failed checkpoint for retries, including an ambiguous network failure.
export class HistoryWriter {
  private pending: ChatHistoryState | null = null
  private running = false
  private failed = false
  private attempt: ChatHistoryState | null = null
  private saved: string

  constructor(private token: string, private revision: number, state: ChatHistoryState | null,
    private onStatus: (saving: boolean, error: string) => void) {
    this.saved = JSON.stringify(state)
  }

  enqueue(state: ChatHistoryState) {
    this.pending = state
    if (!this.failed) void this.flush()
  }

  retry() { this.failed = false; void this.flush() }

  private async flush() {
    if (this.running) return
    this.running = true
    this.onStatus(true, '')
    try {
      while (this.attempt || this.pending) {
        const state = this.attempt || this.pending!
        this.attempt = state
        const serialized = JSON.stringify(state)
        if (serialized !== this.saved) {
          const result = await guestRequest<ChatHistory>('/v1/deposit/chat-history', this.token, {
            method: 'PUT', body: JSON.stringify({ expected_revision: this.revision, state }),
          })
          this.revision = result.revision
          this.saved = serialized
        }
        if (this.pending === state) this.pending = null
        this.attempt = null
      }
      this.onStatus(false, '')
    } catch (error) {
      this.failed = true
      this.onStatus(false, error instanceof Error ? error.message : 'Sauvegarde impossible.')
    } finally { this.running = false }
  }
}
