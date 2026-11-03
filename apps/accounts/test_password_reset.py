from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from apps.accounts.forms import BantayPlakaPasswordResetForm
from config import settings as project_settings


@override_settings(
    PASSWORD_RESET_DOMAIN='bantay-plaka-production-3246.up.railway.app',
)
class PasswordResetDomainTests(SimpleTestCase):
    def test_password_reset_uses_django_smtp_backend(self):
        self.assertEqual(
            project_settings.EMAIL_BACKEND,
            'django.core.mail.backends.smtp.EmailBackend',
        )

    def test_reset_email_uses_configured_domain_instead_of_request_host(self):
        form = BantayPlakaPasswordResetForm(data={'email': 'reset@example.com'})
        self.assertTrue(form.is_valid(), form.errors)

        user = SimpleNamespace(
            pk=1,
            email='reset@example.com',
            password='encoded-password',
            last_login=None,
        )
        request = RequestFactory().get(
            '/',
            secure=True,
            HTTP_HOST='bantay-plaka-production.up.railway.app',
        )
        with (
            patch.object(form, 'get_users', return_value=[user]),
            patch.object(form, 'send_mail') as send_mail,
        ):
            form.save(
                use_https=request.is_secure(),
                request=request,
                token_generator=Mock(make_token=Mock(return_value='reset-token')),
            )

        email_context = send_mail.call_args.args[2]
        self.assertEqual(
            email_context['domain'],
            'bantay-plaka-production-3246.up.railway.app',
        )
        self.assertEqual(email_context['protocol'], 'https')

    def test_password_reset_view_uses_the_canonical_domain_form(self):
        view = resolve(reverse('password_reset')).func

        self.assertIs(
            view.view_initkwargs['form_class'],
            BantayPlakaPasswordResetForm,
        )
