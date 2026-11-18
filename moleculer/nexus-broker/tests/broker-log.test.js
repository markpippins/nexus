/**
 * Broker log capture — unit cases.
 *
 * `tests/helpers/broker-log.js` exists because all four broker-booting files
 * used `stdio: 'ignore'`, so a failed boot reported only "broker did not become
 * healthy within 30s" and the broker's actual reason went to /dev/null. That
 * made one CI job red for five commits while the cause (a status query failing
 * on a column the test table lacked) sat in a discarded stream.
 *
 * The properties that matter are pinned here, with a real child process and a
 * real file — not a mock: output is captured, the exit status is reported, the
 * log path is named, an empty log says so, and closing the parent's descriptor
 * after the spawn does not detach the child's output. No broker, no Mongo, no
 * PostgreSQL, so this runs in the cheap CI step.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const { createBrokerLog } = require('./helpers/broker-log')

/** Spawn a short-lived child writing to the log; resolve on exit. */
function runWithLog(log, script) {
  const fd = log.open()
  const child = spawn(process.execPath, ['-e', script], { stdio: ['ignore', fd, fd] })
  log.close(fd)
  return new Promise((resolve, reject) => {
    child.on('error', reject)
    child.on('exit', () => resolve(child))
  })
}

test('a failed child explains itself: output, exit status and log path', async () => {
  const log = createBrokerLog('broker-log-unit')
  const child = await runWithLog(
    log,
    'console.error("fatal: cannot start services"); process.exit(3)',
  )

  const message = log.describe('broker did not become healthy within 30s', child)

  assert.match(message, /^broker did not become healthy within 30s \(exit=3 signal=null\)/)
  assert.match(message, /--- broker output ---/)
  assert.match(message, /fatal: cannot start services/, 'the child’s stderr must survive')
  assert.ok(message.includes(log.path), 'the message must name the log so a runner artifact can be fetched')
})

test('an empty log says so instead of reporting blank output', async () => {
  const log = createBrokerLog('broker-log-empty')
  await runWithLog(log, 'process.exit(0)')
  assert.match(log.tail(), /produced no output/)
  assert.match(log.tail(), /broker-log-empty/)
})

test('closing the parent descriptor after the spawn keeps late output', async () => {
  const log = createBrokerLog('broker-log-close')
  // Writes after the parent's close: the child holds its own descriptor, so a
  // close in this process must not truncate or detach anything.
  await runWithLog(log, 'setTimeout(() => { console.log("late line"); }, 100)')
  assert.match(log.tail(), /late line/)
})
