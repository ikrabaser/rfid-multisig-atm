"""V4.2 operasyon konsolu smoke testleri."""
from django.contrib.auth import get_user_model
from django.test import TestCase


class CommandCenterV42Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="v42_test_user",
            password="test-only-password",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_dashboard_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operasyon Merkezi")
        self.assertContains(response, "ATM kontrol masası")
        self.assertContains(response, "İnceleme masası")
        self.assertContains(response, "ATM Laboratuvarı")
        self.assertContains(response, "Politika bütünlüğü")
        self.assertContains(response, "command-center-v42.css")
        self.assertContains(response, "command-center-v42.js")
        self.assertContains(response, 'id="denetim"')

    def test_anonymous_user_cannot_open_dashboard(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/giris/"))