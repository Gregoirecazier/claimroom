import { describe, expect, it } from 'vitest'
import { updateVoiceTranscript, type VoiceTurn } from './voiceTranscript'

describe('live Vapi conversation', () => {
  it('uses committed conversation updates to include both sides', () => {
    const turns = updateVoiceTranscript([], { type: 'conversation-update', messages: [
      { role: 'system', message: 'Internal prompt' },
      { role: 'bot', message: 'Bonjour, comment puis-je vous aider ?' },
      { role: 'user', message: 'Un accident.' },
      { role: 'bot', message: 'Où ?' },
    ] })
    expect(turns).toEqual([
      { role: 'assistant', text: 'Bonjour, comment puis-je vous aider ?' },
      { role: 'user', text: 'Un accident.' },
      { role: 'assistant', text: 'Où ?' },
    ])
    expect(updateVoiceTranscript(turns, { type: 'conversation-update', messages: [
      { role: 'bot', message: 'Bonjour, comment puis-je vous aider ?' },
      { role: 'user', message: 'Un accident.' },
      { role: 'bot', message: 'Où exactement ?' },
    ] })).toHaveLength(3)
  })

  it('supports OpenAI formatted snapshots and preserves repeated real turns', () => {
    expect(updateVoiceTranscript([], { type: 'conversation-update', messagesOpenAIFormatted: [
      { role: 'assistant', content: 'Bonjour.' },
      { role: 'assistant', content: 'Bonjour.' },
      { role: 'user', content: 'Bonjour.' },
    ] })).toEqual([
      { role: 'assistant', text: 'Bonjour.' },
      { role: 'assistant', text: 'Bonjour.' },
      { role: 'user', text: 'Bonjour.' },
    ])
  })

  it('accepts final transcript and speech events without duplicating the same turn', () => {
    let turns: VoiceTurn[] = []
    turns = updateVoiceTranscript(turns, { type: 'assistant.speechStarted', text: 'Bonjour.', turn: 1 })
    turns = updateVoiceTranscript(turns, { type: 'transcript', transcriptType: 'final', role: 'assistant', transcript: 'Bonjour.' })
    turns = updateVoiceTranscript(turns, { type: "transcript[transcriptType='final']", transcriptType: 'final', role: 'user', transcript: 'Un accident.' })
    expect(turns).toEqual([{ role: 'assistant', text: 'Bonjour.', turn: 1 }, { role: 'user', text: 'Un accident.' }])
  })

  it('keeps the spoken opening when Vapi omits it from conversation snapshots', () => {
    let turns: VoiceTurn[] = []
    turns = updateVoiceTranscript(turns, { type: 'assistant.speechStarted', text: 'Bonjour,', turn: 0 })
    turns = updateVoiceTranscript(turns, { type: 'assistant.speechStarted', text: 'Bonjour, cet appel est enregistré.', turn: 0 })
    turns = updateVoiceTranscript(turns, { type: 'transcript', transcriptType: 'final', role: 'user', transcript: 'J’ai eu un accident.' })
    turns = updateVoiceTranscript(turns, { type: 'conversation-update', messages: [
      { role: 'system', message: 'Internal prompt' },
      { role: 'user', message: 'J’ai eu un accident.' },
    ] })
    expect(turns).toEqual([
      { role: 'assistant', text: 'Bonjour, cet appel est enregistré.', turn: 0 },
      { role: 'user', text: 'J’ai eu un accident.' },
    ])
  })
})
