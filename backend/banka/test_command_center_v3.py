"""V3: salt okunur kontrol kulesi görüntüleme ve erişim smoke testleri."""
from django.contrib.auth import get_user_model
from django.test import TestCase


class CommandCenterV3Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ccv3_test", password="test-only-password", is_staff=True
        )
        self.client.force_login(self.user)

    def test_staff_can_view_rebuilt_control_tower(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kontrol Kulesi")
        self.assertContains(response, "Operasyon Merkezi")
        self.assertContains(response, "primary-nav")
        self.assertContains(response, "command-center-v3.css")
        self.assertContains(response, "command-center-v3.js")
        self.assertContains(response, "CANLI BAĞLANTI VERİSİ DEĞİL")
        self.assertContains(response, "Güvenlik ve bütünlük")

    def test_anonymous_user_is_redirected(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
