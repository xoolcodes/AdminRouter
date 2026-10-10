import tempfile
import unittest
from unittest import mock
from pathlib import Path

import setup_adminrouter


class SetupAdminRouterTests(unittest.TestCase):
    def test_hash_password_uses_pbkdf2_format(self):
        with mock.patch("setup_adminrouter.os.urandom", return_value=b"\x01" * 16):
            hashed = setup_adminrouter.hash_password("secret")
        self.assertTrue(hashed.startswith("pbkdf2_sha256$390000$"))
        self.assertEqual(len(hashed.split("$")), 4)

    def test_verify_password_checks_the_hash(self):
        with mock.patch("setup_adminrouter.os.urandom", return_value=b"\x01" * 16):
            hashed = setup_adminrouter.hash_password("secret")
        self.assertTrue(setup_adminrouter.verify_password("secret", hashed))
        self.assertFalse(setup_adminrouter.verify_password("wrong", hashed))

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
