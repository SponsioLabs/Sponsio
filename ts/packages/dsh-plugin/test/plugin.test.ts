/**
 * The plugin's contract with the harness, exercised through a stand-in for
 * the pre-execute waterfall. The SDK behind it is real: these assertions
 * fail if enforcement, mode, or delegation regress.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply, type Config } from '../src/index'

const RULEBOOK = `version: "1"
agents:
  mailer:
    contracts:
      - G: "tool \`verify_address\` must precede \`send_email\`"
        desc: verify the address before sending to it
      - G: "tool \`send_email\` at most 2 times"
        desc: at most two emails per session
`

function rulebook(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-sponsio-'))
  const path = join(dir, 'sponsio.yaml')
  writeFileSync(path, RULEBOOK)
  return path
}

type Decision = { kind: string; reason?: string }

/**
 * The waterfall: listeners run in registration order and each may decide or
 * delegate. `tail` stands for whatever the harness would do after every
 * listener has passed, so a test can prove the plugin DELEGATED rather than
 * merely allowed.
 */
function harness(config: Partial<Config>, tail: Decision = { kind: 'allow' }) {
  const listeners: Array<(exec: unknown, next: () => Promise<Decision>) => Promise<Decision>> = []
  const ctx = {
    on(event: string, fn: (exec: unknown, next: () => Promise<Decision>) => Promise<Decision>) {
      if (event === 'tools/pre-execute') listeners.push(fn)
    },
  }
  apply(ctx as never, {
    config: rulebook(),
    agentId: 'mailer',
    mode: 'enforce',
    exclude: [],
    ...config,
  } as Config)

  return async function call(name: string, args: Record<string, unknown> = {}): Promise<Decision> {
    let i = 0
    const exec = { name, arguments: args, callId: 'c', signal: new AbortController().signal }
    const next = async (): Promise<Decision> =>
      i < listeners.length ? listeners[i++]!(exec, next) : tail
    return next()
  }
}

test('enforce denies a call the rulebook forbids, and allows it once the trace earns it', async () => {
  const call = harness({ mode: 'enforce' })
  assert.equal((await call('send_email')).kind, 'deny')
  assert.equal((await call('verify_address')).kind, 'allow')
  assert.equal((await call('send_email')).kind, 'allow')
})

test('a temporal rule counts across calls', async () => {
  const call = harness({ mode: 'enforce' })
  await call('verify_address')
  assert.equal((await call('send_email')).kind, 'allow')
  assert.equal((await call('send_email')).kind, 'allow')
  assert.equal((await call('send_email')).kind, 'deny', 'third send passed a cap of 2')
})

test('observe stops nothing', async () => {
  const call = harness({ mode: 'observe' })
  assert.equal((await call('send_email')).kind, 'allow')
})

test('the deny reason is a sentence the model can act on', async () => {
  const call = harness({ mode: 'enforce' })
  const decision = await call('send_email')
  assert.equal(decision.kind, 'deny')
  assert.match(decision.reason ?? '', /send_email/)
  assert.doesNotMatch(
    decision.reason ?? '',
    /Blocked by a Sponsio contract: The action/,
    'the prefix doubles up on agentMsg, which is already a finished sentence',
  )
})

test('an excluded tool is transparent', async () => {
  const call = harness({ mode: 'enforce', exclude: ['send_*'] })
  assert.equal((await call('send_email')).kind, 'allow')
})

test('a clean verdict delegates instead of allowing', async () => {
  // The tail stands for a listener registered after this plugin. If the
  // plugin returned `allow` itself, that listener would never run and this
  // plugin would silently overrule every guard downstream of it.
  const call = harness({ mode: 'enforce' }, { kind: 'ask', reason: 'downstream' })
  const decision = await call('verify_address')
  assert.equal(decision.kind, 'ask')
  assert.equal(decision.reason, 'downstream')
})

test('an unreadable rulebook fails at load, not at the first call', () => {
  assert.throws(() => {
    apply({ on() {} } as never, {
      config: '/nonexistent/sponsio.yaml',
      agentId: 'mailer',
      mode: 'enforce',
      exclude: [],
    } as Config)
  })
})
