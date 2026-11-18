/**
 * Broker boot diagnostics for the harness files that spawn the real broker.
 *
 * Why
 * ---
 * Four test files boot `moleculer-runner dist/services` and every one of them
 * used `stdio: ['ignore', 'ignore', 'ignore']`. The intent was sound — a chatty
 * service must never fill a pipe and block the child before the test reaches
 * its assertions — but the effect was that a boot failure arrived as the bare
 * string "broker did not become healthy within 30s" with the broker's own
 * reason discarded. A job can be red for five commits that way: the keychain
 * delta step was, and the cause (the broker's `/status` query failing on a
 * column the test table did not have) only surfaced once this captured the
 * child's output.
 *
 * A regular file keeps both properties: writes to a file descriptor do not
 * block on a reader, and the bytes survive the failure.
 *
 * Usage
 * -----
 *   const brokerLog = createBrokerLog('broker-smoke')   // once per file
 *   const fd = brokerLog.open()
 *   child = spawn(cmd, args, { ..., stdio: ['ignore', fd, fd] })
 *   brokerLog.close(fd)                                  // child keeps its copies
 *   ...
 *   throw new Error(brokerLog.describe('broker did not become healthy within 30s', child))
 *
 * `describe()` appends the child's exit status and the tail of its output, so a
 * timeout says whether the broker crashed (and why) or merely hung.
 */
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const DEFAULT_TAIL_LINES = 40

/**
 * Log handle for one test file's broker. `name` distinguishes the files in
 * /tmp when several run concurrently; the pid distinguishes repeat runs.
 */
function createBrokerLog(name) {
  const logPath = path.join(os.tmpdir(), `${name}-broker-${process.pid}.log`)

  /** Tail of the broker's own output, for a failure that needs explaining. */
  const tail = (lines = DEFAULT_TAIL_LINES) => {
    try {
      const text = fs.readFileSync(logPath, 'utf8').trimEnd()
      if (!text) return `(broker produced no output; log: ${logPath})`
      return `${text.split('\n').slice(-lines).join('\n')}\n(log: ${logPath})`
    } catch (err) {
      return `(broker log unreadable: ${err.message}; log: ${logPath})`
    }
  }

  return {
    path: logPath,
    /** Descriptor for the spawn's stdout/stderr; one fd keeps the order. */
    open: () => fs.openSync(logPath, 'w'),
    /**
     * Close this process's copies after the spawn. The child holds its own, so
     * closing here does not truncate or detach its output, and leaving two
     * independent 'w' descriptors open would interleave at separate offsets.
     */
    close: (fd) => {
      try {
        fs.closeSync(fd)
      } catch {
        /* already closed */
      }
    },
    tail,
    /** Timeout message with the child's exit status and output tail attached. */
    describe: (message, child = null) => {
      const status = child ? ` (exit=${child.exitCode} signal=${child.signalCode})` : ''
      return `${message}${status}\n--- broker output ---\n${tail()}`
    },
  }
}

module.exports = { createBrokerLog, DEFAULT_TAIL_LINES }
