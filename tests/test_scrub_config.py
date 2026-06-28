import tempfile
import unittest
from pathlib import Path

from scripts.scrub_config import REDACTED, scrub_toml_file


class TestScrubConfig(unittest.TestCase):
    def test_redacts_nested_secrets_and_uri_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "model.toml"
            target = root / "model.redacted.toml"
            source.write_text(
                '\n'.join([
                    '[llm.openai]',
                    'api_key = "sk-real"',
                    'base_url = "https://api.openai.com/v1"',
                    '',
                    '[postgresql]',
                    'password = "root-password"',
                    'uri = "postgresql://user:secret@localhost:5432/root"',
                    '',
                    '[integrations.feishu]',
                    'verification_token = "token-real"',
                    'app_secret = "secret-real"',
                    '',
                    '[runtime]',
                    'max_tokens = 4096',
                ]),
                encoding="utf-8",
            )

            scrub_toml_file(source, target)
            text = target.read_text(encoding="utf-8")

        self.assertIn(REDACTED, text)
        self.assertIn("postgresql://<redacted>@localhost:5432/root", text)
        self.assertNotIn("sk-real", text)
        self.assertNotIn("root-password", text)
        self.assertNotIn("token-real", text)
        self.assertNotIn("secret-real", text)
        self.assertIn("max_tokens = 4096", text)


if __name__ == "__main__":
    unittest.main()
