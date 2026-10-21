"""Exercise the Node recipe with disposable children, never databases or services."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'examples/scripts/run-probe.cjs'


@unittest.skipUnless(shutil.which('node'), 'Optional Node recipe requires Node.js')
class ChildProbeTests(unittest.TestCase):
    def probe(self, script, **options):
        options = {'timeout': 2000, 'name': 'fixture', **options}
        wrapper = (f'const {{runProbe}} = require({json.dumps(str(RECIPE))});'
                   f'runProbe(["-e", {json.dumps(script)}], {json.dumps(options)})'
                   '.then(r => console.log(JSON.stringify(r)))'
                   '.catch(e => {console.log(JSON.stringify({message:e.message,diagnostics:e.diagnostics}));process.exitCode=1});')
        result = subprocess.run(['node', '-e', wrapper], text=True, capture_output=True, timeout=10)
        return result.returncode, json.loads(result.stdout)

    def test_success_retains_exact_stdout_and_stderr(self):
        code, result = self.probe('process.stdout.write("yes");process.stderr.write("warning")')
        self.assertEqual(code, 0)
        self.assertEqual(result, {'stdout': 'yes', 'stderr': 'warning'})

    def test_nonzero_exit_includes_status_and_both_streams_not_argv(self):
        code, result = self.probe('console.log("progress");console.error("reason");process.exit(7)')
        self.assertEqual(code, 1)
        self.assertEqual(result['diagnostics']['code'], 7)
        self.assertIn('progress', result['message'])
        self.assertIn('reason', result['message'])
        self.assertNotIn('process.exit(7)', result['message'])
        self.assertFalse(result['diagnostics']['timedOut'])

    def test_timeout_is_not_reported_as_assertion_failure(self):
        code, result = self.probe('setTimeout(()=>{},60000)', timeout=50)
        self.assertEqual(code, 1)
        self.assertTrue(result['diagnostics']['killed'])
        self.assertTrue(result['diagnostics']['timedOut'])
        self.assertEqual(result['diagnostics']['timeoutMs'], 50)

    def test_signal_is_preserved(self):
        code, result = self.probe('process.kill(process.pid,"SIGTERM")')
        self.assertEqual(code, 1)
        self.assertEqual(result['diagnostics']['signal'], 'SIGTERM')
        self.assertFalse(result['diagnostics']['timedOut'])

    def test_credentials_and_url_password_are_redacted(self):
        code, result = self.probe(
            'console.error(process.env.PROBE_TOKEN,process.env.DATABASE_URL,"fixture-db-password");process.exit(1)',
            env={'PROBE_TOKEN': 'fixture-token-value',
                 'DATABASE_URL': 'postgresql://fixture:fixture-db-password@localhost/test'})
        self.assertEqual(code, 1)
        self.assertNotIn('fixture-token-value', result['message'])
        self.assertNotIn('fixture-db-password', result['message'])
        self.assertIn('[redacted]', result['message'])

    def test_max_buffer_failure_is_bounded_not_a_timeout(self):
        code, result = self.probe('console.log("x".repeat(30000))', maxBuffer=10000)
        self.assertEqual(code, 1)
        self.assertEqual(result['diagnostics']['code'], 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER')
        self.assertFalse(result['diagnostics']['timedOut'])
        self.assertLess(len(result['message']), 17000)

    def test_invalid_timeout_fails_before_launching(self):
        code, result = self.probe('process.exit(7)', timeout=0)
        self.assertEqual(code, 1)
        self.assertIn('finite positive', result['message'])
        self.assertNotIn('diagnostics', result)


if __name__ == '__main__':
    unittest.main()
