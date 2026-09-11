"""Interactive wizard tests: pseudo-terminals and dummy secrets only; no network."""
import os
from pathlib import Path
import select
import shutil
import subprocess
import time
import unittest

try:
    import pty
except ImportError:
    pty = None

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'configure.sh'


@unittest.skipUnless(pty and os.name == 'posix', 'PTY tests require POSIX')
class ConfigureTests(unittest.TestCase):
    def interactive(self, shell, tracing=False):
        binary = shutil.which(shell)
        if not binary:
            self.skipTest(shell + ' is not installed')
        master, slave = pty.openpty()
        key = 'wizard-test-token-not-a-secret'
        command = ('set -x; ' if tracing else '') + 'source "$1"; code=$?; set +x; printf "\\nWIZARD_EXIT=%s\\n" "$code"; test -n "$RELAY_IMAGE_API_KEY" && printf "KEY_PRESENT\\n"; exit "$code"'
        args = [binary, '-f' if shell == 'zsh' else '--noprofile', '-c', command, 'wizard-test', str(SCRIPT)]
        env = {k: v for k, v in os.environ.items() if not k.startswith('RELAY_IMAGE_')}
        process = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave, env=env)
        os.close(slave)
        data = b''
        stage = 0
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        block = os.read(master, 8192)
                    except OSError:
                        break
                    if not block:
                        break
                    data += block
                text = data.decode('utf-8', errors='replace')
                if stage == 0 and '请输入你信任的中转站 API 基础 URL：' in text:
                    os.write(master, b'https://relay.example.com/v1\n')
                    stage = 1
                if stage == 1 and '请输入 API Key（输入不显示）：' in text:
                    # Give read -s a moment to disable terminal echo after its prompt.
                    time.sleep(0.1)
                    os.write(master, (key + '\n').encode())
                    stage = 2
                if process.poll() is not None and not ready:
                    break
            process.wait(timeout=2)
            text = data.decode('utf-8', errors='replace')
            self.assertEqual(process.returncode, 0, text)
            self.assertIn('WIZARD_EXIT=0', text)
            self.assertIn('KEY_PRESENT', text)
            self.assertNotIn(key, text)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_bash_exports_to_caller(self):
        self.interactive('bash')

    def test_zsh_exports_to_caller(self):
        self.interactive('zsh')

    def test_bash_xtrace_does_not_expose_key(self):
        self.interactive('bash', tracing=True)

    def test_zsh_xtrace_does_not_expose_key(self):
        self.interactive('zsh', tracing=True)

    def test_direct_execution_refused(self):
        for shell in ('bash', 'zsh'):
            binary = shutil.which(shell)
            if binary:
                p = subprocess.run([binary, str(SCRIPT)], stdin=subprocess.DEVNULL, capture_output=True)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(b'source', p.stderr)

    def test_noninteractive_source_refused(self):
        binary = shutil.which('bash')
        if not binary:
            self.skipTest('bash is unavailable')
        p = subprocess.run([binary, '-c', 'source "$1"', 'test', str(SCRIPT)], stdin=subprocess.DEVNULL, capture_output=True)
        self.assertNotEqual(p.returncode, 0)


if __name__ == '__main__':
    unittest.main()
