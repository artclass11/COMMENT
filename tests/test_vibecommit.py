import importlib.util
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vibecommit", ROOT / "VibeCommit.py")
vc = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["vibecommit"] = vc
SPEC.loader.exec_module(vc)

class VibeCommitTests(unittest.TestCase):
    def test_conventional_commit_validation(self):
        valid = ["feat(api): add rate limiting", "fix!: remove unsafe fallback", "refactor(core)!: simplify parser", "docs: update installation guide"]
        invalid = ["feature: bad type", "feat: ends with period.", "feat: " + "x" * 73]
        for msg in valid:
            self.assertTrue(vc.validate_commit_message(msg), msg)
        for msg in invalid:
            self.assertFalse(vc.validate_commit_message(msg), msg)

    def test_extract_commit_line(self):
        self.assertEqual(vc.extract_commit_line("```text\nfeat(api): add tests\n```"), "feat(api): add tests")
        self.assertEqual(vc.extract_commit_line("commit message: fix: handle empty input"), "fix: handle empty input")

    def test_llm_response(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"choices":[{"message":{"content":"feat(api): add validation"}}]}'
        with patch("vibecommit.urlopen", return_value=Response()):
            msg = vc.call_llm("add validation", "diff", url="http://localhost/test", model="test", api_key=None, timeout=2)
        self.assertEqual(msg, "feat(api): add validation")

    def test_audio_cleanup_path(self):
        fake_sd = types.SimpleNamespace(rec=lambda *a, **k: [[0]], wait=lambda: None)
        fake_wav = types.SimpleNamespace(write=lambda *a, **k: Path(a[0]).write_bytes(b"RIFF"))
        with patch.dict(sys.modules, {"sounddevice": fake_sd, "scipy": types.SimpleNamespace(), "scipy.io": types.SimpleNamespace(), "scipy.io.wavfile": fake_wav}):
            path = vc.record_audio(0.01, 8000)
            self.assertTrue(path.exists())
            path.unlink()

if __name__ == "__main__":
    unittest.main()