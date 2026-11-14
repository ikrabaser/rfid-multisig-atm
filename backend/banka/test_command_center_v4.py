"""V4: operasyon arayüzünün yalnızca salt-okunur ekran ve erişim testleri."""
from django.contrib.auth import get_user_model
from django.test import TestCase


class CommandCenterV4Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ccv4_test", password="test-only-password", is_staff=True
        )
        self.client.force_login(self.user)

    def test_staff_gets_current_command_center_not_the_old_v4_assets(self):
        """V4 arayüzünün yerini V4.2 aldı (ayrıntılı ekran testleri test_command_center_v42.py'de): eski varlıklar sunulmaz."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operasyon Merkezi")
        self.assertContains(response, "command-center-v42.css")
        self.assertContains(response, "terminal-search-v42")
        self.assertContains(response, "Politika bütünlüğü")
        self.assertNotContains(response, "RFID CORE")
        self.assertNotContains(response, "command-center-v4.css")
        self.assertNotContains(response, "command-center-v3.css")

    def test_anonymous_user_redirected_to_login(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/giris/"))

    def test_non_staff_user_is_forbidden(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        response = self.client.get("/")
        self.assertEqual(response.status_code, 403)
