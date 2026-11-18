/**
 * Ollama generation options — unit tests.
 *
 * The ceiling is deployment configuration, so the contract under test is:
 * absent/invalid input must preserve the historical default (1024) and valid
 * input must be used verbatim (floored to an integer). No network, no PG.
 */
const { test, describe } = require('node:test')
const assert = require('node:assert/strict')

const {
  DEFAULT_OLLAMA_NUM_PREDICT,
  resolveOllamaNumPredict,
  resolveOllamaNumPredictFromEnv,
} = require('../lib/ollama-options.ts')

describe('resolveOllamaNumPredict', () => {
  test('default is unchanged from the previous hardcoded value', () => {
    assert.equal(DEFAULT_OLLAMA_NUM_PREDICT, 1024)
  })

  test('absent values fall back to the default', () => {
    for (const raw of [undefined, null, '']) {
      assert.equal(resolveOllamaNumPredict(raw), DEFAULT_OLLAMA_NUM_PREDICT)
    }
  })

  test('invalid values fall back to the default instead of reaching ollama', () => {
    for (const raw of ['abc', 'NaN', 'Infinity', '0', '-1', '-0.5']) {
      assert.equal(resolveOllamaNumPredict(raw), DEFAULT_OLLAMA_NUM_PREDICT)
    }
  })

  test('valid values are honoured', () => {
    assert.equal(resolveOllamaNumPredict('256'), 256)
    assert.equal(resolveOllamaNumPredict('1'), 1)
    assert.equal(resolveOllamaNumPredict(4096), 4096)
  })

  test('fractional values are floored to an integer', () => {
    assert.equal(resolveOllamaNumPredict('256.9'), 256)
  })
})

describe('resolveOllamaNumPredictFromEnv', () => {
  test('reads OLLAMA_NUM_PREDICT', () => {
    assert.equal(resolveOllamaNumPredictFromEnv({ OLLAMA_NUM_PREDICT: '256' }), 256)
  })

  test('an unset variable keeps the default', () => {
    assert.equal(resolveOllamaNumPredictFromEnv({}), DEFAULT_OLLAMA_NUM_PREDICT)
  })
})
