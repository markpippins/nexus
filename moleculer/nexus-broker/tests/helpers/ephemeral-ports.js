/**
 * Ephemeral OS-assigned port allocation for broker test harnesses.
 *
 * Why: fixed test ports collide with anything else on the machine, and a
 * collision produces the worst failure mode — the health poll connects to
 * whatever else holds the port and the suite runs against the wrong server
 * (observed twice in 2026-09: titanium dev servers serving SPA-fallback 200s
 * on the broker's test ports, and a docker-proxy surface impersonating the
 * legacy execution API). An ephemeral port from the OS is collision-free by
 * construction and makes concurrent `node --test` files safe without a
 * hand-maintained port map.
 *
 * Mechanism: bind a server to port 0, read the assigned port, close, return
 * it. There is a theoretical TOCTOU window between close and re-bind; it is
 * negligible in practice (the kernel only hands the port out once per
 * allocation pass and re-binds are immediate) and strictly better than any
 * fixed port. Set HARNESS_FIXED_PORTS=1 to fall back to the historical
 * fixed ports for debugging a specific port interaction.
 */
const net = require('node:net')

async function getEphemeralPort(fallbackFixedPort) {
  if (process.env.HARNESS_FIXED_PORTS === '1' && fallbackFixedPort) {
    return Number(fallbackFixedPort)
  }
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

module.exports = { getEphemeralPort }
