import unittest

from crypto_perp_tool.web.network import dashboard_urls, normalize_bind_host


class NetworkTests(unittest.TestCase):
    def test_mobile_mode_binds_to_all_interfaces(self):
        self.assertEqual(normalize_bind_host(host=None, mobile=True), "0.0.0.0")

    def test_default_mode_binds_to_loopback(self):
        self.assertEqual(normalize_bind_host(host=None, mobile=False), "127.0.0.1")

    def test_dashboard_urls_include_lan_urls_when_bound_to_all_interfaces(self):
        urls = dashboard_urls("0.0.0.0", 8000, lan_ips=["192.168.1.8", "10.0.0.5"])

        self.assertEqual(urls["local"], "http://127.0.0.1:8000")
        self.assertEqual(
            urls["lan"],
            ["http://192.168.1.8:8000", "http://10.0.0.5:8000"],
        )

    def test_dashboard_urls_hide_lan_urls_for_loopback_bind(self):
        urls = dashboard_urls("127.0.0.1", 8000, lan_ips=["192.168.1.8"])

        self.assertEqual(urls["lan"], [])


if __name__ == "__main__":
    unittest.main()
