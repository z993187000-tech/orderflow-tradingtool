import base64
import unittest

from crypto_perp_tool.web.auth import is_authorized, required_auth_header


class AuthTests(unittest.TestCase):
    def test_allows_when_password_is_not_configured(self):
        self.assertTrue(is_authorized(headers={}, password=None))
        self.assertTrue(is_authorized(headers={}, password=""))

    def test_rejects_missing_header_when_password_is_configured(self):
        self.assertFalse(is_authorized(headers={}, password="secret"))

    def test_accepts_basic_auth_password(self):
        token = base64.b64encode(b"admin:secret").decode("ascii")

        self.assertTrue(is_authorized(headers={"Authorization": f"Basic {token}"}, password="secret"))

    def test_rejects_wrong_basic_auth_password(self):
        token = base64.b64encode(b"admin:wrong").decode("ascii")

        self.assertFalse(is_authorized(headers={"Authorization": f"Basic {token}"}, password="secret"))

    def test_required_auth_header_declares_basic_realm(self):
        self.assertEqual(required_auth_header(), 'Basic realm="Order Flow Dashboard"')


if __name__ == "__main__":
    unittest.main()
