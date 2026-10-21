'use strict';

const { execFile } = require('node:child_process');
const { performance } = require('node:perf_hooks');
const { promisify } = require('node:util');

const execute = promisify(execFile);

function redact(value, env) {
  let text = String(value ?? '');
  for (const [name, secret] of Object.entries(env)) {
    if (secret && /(?:TOKEN|SECRET|PASSWORD|PRIVATE_KEY|AUTHORIZATION|COOKIE|DATABASE_URL)/i.test(name)) {
      text = text.split(secret).join('[redacted]');
      // A child may log a database password separately from its URL.
      if (/URL/i.test(name)) {
        try {
          const password = decodeURIComponent(new URL(secret).password);
          if (password) text = text.split(password).join('[redacted]');
        } catch { /* Not every secret is a URL. */ }
      }
    }
  }
  text = text.replace(/\b((?:postgres(?:ql)?|https?|redis):\/\/)[^\s/@]+(?::[^\s@]*)?@/gi,
    '$1[redacted]@');
  return text;
}

/** Run a bounded Node probe without shell evaluation or secret-bearing argv logs. */
async function runProbe(args, options) {
  if (!options || !Number.isFinite(options.timeout) || options.timeout <= 0) {
    throw new TypeError('A finite positive probe timeout is required');
  }
  const { name = 'node-probe', timeout, cwd, env = process.env,
    maxBuffer = 1024 * 1024 } = options;
  if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string') ||
      !Number.isFinite(maxBuffer) || maxBuffer <= 0) {
    throw new TypeError('Probe requires string arguments and a finite positive buffer limit');
  }
  const started = performance.now();
  try {
    return await execute(process.execPath, args, { timeout, cwd, env, maxBuffer, encoding: 'utf8' });
  } catch (failure) {
    const elapsedMs = Math.round(performance.now() - started);
    const code = typeof failure.code === 'number' || /^[A-Z0-9_]+$/.test(failure.code ?? '')
      ? failure.code : null;
    const signal = /^SIG[A-Z0-9]+$/.test(failure.signal ?? '') ? failure.signal : null;
    const diagnostics = {
      code, signal, killed: failure.killed === true, elapsedMs, timeoutMs: timeout,
      timedOut: failure.killed === true && code !== 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' && elapsedMs >= timeout,
    };
    const excerpt = value => redact(value, env).slice(-8000);
    const error = new Error([
      `Probe ${JSON.stringify(redact(name, env))} failed: ${JSON.stringify(diagnostics)}`,
      `stdout (redacted, tail):\n${excerpt(failure.stdout) || '(empty)'}`,
      `stderr (redacted, tail):\n${excerpt(failure.stderr) || '(empty)'}`,
    ].join('\n'));
    // Do not attach the original error as cause: it includes the full argv.
    error.diagnostics = diagnostics;
    throw error;
  }
}

module.exports = { runProbe };
