import { test } from 'node:test'
import assert from 'node:assert/strict'
import { deleteCase, getCase, listCases } from './api.mjs'

test('deleting a demo case removes only that case and rejects unknown IDs', async () => {
  const initial = await listCases()
  await deleteCase('', initial[0].id)
  assert.deepEqual((await listCases()).map(c => c.id), initial.slice(1).map(c => c.id))
  await assert.rejects(getCase('', initial[0].id), /introuvable/)
  await assert.rejects(deleteCase('', initial[0].id), /introuvable/)
  assert.equal((await listCases()).length, initial.length - 1)
})
