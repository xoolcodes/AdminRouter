import tempfile
import unittest
from pathlib import Path

import setup_adminrouter


class SetupAdminRouterTests(unittest.TestCase):
    def test_hash_password_is_sha256(self):
        hashed = setup_adminrouter.hash_password("secret")
        self.assertEqual(
            hashed,
            "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b",
        )

    def test_generate_sql_file_escapes_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.sql"
            setup_adminrouter.generate_sql_file(target, "user'name", "hash'value", 8080)
            content = target.read_text(encoding="utf-8")

        self.assertIn("user''name", content)
        self.assertIn("hash''value", content)
        self.assertIn("8080", content)

    def test_parse_port_bounds(self):
        self.assertEqual(setup_adminrouter.parse_port("1"), 1)
        self.assertEqual(setup_adminrouter.parse_port("65535"), 65535)
        with self.assertRaises(ValueError):
            setup_adminrouter.parse_port("0")
        with self.assertRaises(ValueError):
            setup_adminrouter.parse_port("65536")
        with self.assertRaises(ValueError):
            setup_adminrouter.parse_port("abc")


if __name__ == "__main__":
    unittest.main()
