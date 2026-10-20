from django.test import RequestFactory, TestCase

from apps.accounts.forms import LoginForm
from apps.accounts.models import User


class UsernameOrEmailLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='jane_doe',
            email='jane.doe@example.com',
            password='SecurePass123!',
            role=User.ROLE_RESIDENT,
        )

    def _form(self, identifier):
        request = RequestFactory().post('/login/')
        return LoginForm(
            request=request,
            data={'username': identifier, 'password': 'SecurePass123!'},
        )

    def test_resident_can_log_in_with_username(self):
        form = self._form('jane_doe')

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.get_user(), self.user)

    def test_resident_can_log_in_with_email_address_case_insensitively(self):
        form = self._form('JANE.DOE@EXAMPLE.COM')

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.get_user(), self.user)
