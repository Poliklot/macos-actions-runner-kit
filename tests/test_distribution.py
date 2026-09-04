"""Package hygiene, offline hosted CI and source-archive containment checks."""
import importlib.util
import io
import json
from pathlib import Path
import re
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('packager', ROOT / 'scripts/package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class DistributionTests(unittest.TestCase):
    def test_public_ci_never_targets_a_personal_mac_or_uses_secrets(self):
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            source = path.read_text()
            self.assertNotIn('self-hosted', source)
            self.assertNotIn('secrets.', source)
            self.assertNotIn('pull_request_target', source)
            self.assertNotIn('write-all', source)
            self.assertIn('contents: read', source)
            self.assertIn('os: ubuntu-24.04', source)
            self.assertIn('os: macos-15', source)
            self.assertNotRegex(source, r'run:.*\b(?:sudo|setup|register|start)\b')

    def test_actions_in_workflows_and_examples_are_immutable(self):
        for path in [*(ROOT / '.github/workflows').glob('*.yml'), *(ROOT / 'examples').glob('*.yml')]:
            for reference in re.findall(r'uses:\s+([^\s#]+)', path.read_text()):
                self.assertRegex(reference, r'^[\w-]+/[\w-]+@[0-9a-f]{40}$')

    def test_reproducible_archive_contains_only_manifest_files(self):
        first = package.archive_bytes(ROOT)
        self.assertEqual(first, package.archive_bytes(ROOT))
        with tarfile.open(fileobj=io.BytesIO(first), mode='r:gz') as archive:
            expected = {f'{package.NAME}/{name}' for name, _ in package.manifest_files(ROOT)}
            self.assertEqual(set(archive.getnames()), expected)
            self.assertTrue(all(m.isfile() and m.uid == 0 and m.mtime == 0 for m in archive))
            self.assertNotIn(f'{package.NAME}/tool/runner/config.json', expected)

    def test_unlisted_config_and_credentials_are_not_archived(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'source-manifest.json').write_text('["README.md"]')
            (root / 'README.md').write_text('source')
            for name in ('config.json', '.credentials', '.runner', '.env'):
                (root / name).write_text('not-a-real-secret')
            with tarfile.open(fileobj=io.BytesIO(package.archive_bytes(root)), mode='r:gz') as archive:
                self.assertEqual(archive.getnames(), [f'{package.NAME}/README.md'])

    def test_manifest_refuses_traversal_runtime_and_secret_paths(self):
        for name in ('../escape', '/etc/passwd', './README.md', 'a/../README.md', '.env',
                     '.credentials_rsaparams', '.runner', 'tool/runner/config.json', 'cert.p12',
                     '.git/config', 'actions-runner/run.sh', 'a\\b', 'runtime.log'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'source-manifest.json').write_text(json.dumps([name]))
                with self.assertRaises(ValueError):
                    package.archive_bytes(root)

    def test_manifest_refuses_source_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'source-manifest.json').write_text('["linked/source.txt"]')
            (root / 'outside').mkdir()
            (root / 'outside/source.txt').write_text('private')
            (root / 'linked').symlink_to(root / 'outside', target_is_directory=True)
            with self.assertRaises(ValueError):
                package.archive_bytes(root)

    def test_markdown_links_point_to_existing_local_files(self):
        for path in ROOT.rglob('*.md'):
            for target in re.findall(r'\]\(([^ )]+)(?:\s+[^)]*)?\)', path.read_text()):
                if target.startswith(('https://', 'http://', '#')):
                    continue
                self.assertTrue((path.parent / target.split('#')[0]).is_file(), (path, target))

    def test_setup_comes_after_directory_and_configure_in_both_readmes(self):
        for name in ('README.md', 'README.ru.md'):
            source = (ROOT / name).read_text()
            self.assertLess(source.index('cd "$HOME/Downloads/'), source.index('bash runner configure'))
            self.assertLess(source.index('bash runner configure'), source.index('bash runner setup'))
            self.assertRegex(source, r'sudo -iu ci\n```\n[\s\S]+?```bash\nci-runner register')
