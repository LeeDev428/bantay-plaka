from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from apps.core.email_backend import ResendEmailBackend


@override_settings(
    EMAIL_BACKEND='apps.core.email_backend.ResendEmailBackend',
    RESEND_API_KEY='re_test_key',
    DEFAULT_FROM_EMAIL='BantayPlaka <noreply@example.com>',
)
class ResendEmailBackendTests(SimpleTestCase):
    @patch('apps.core.email_backend.requests.post')
    def test_django_email_send_uses_resend_backend(self, post):
        post.return_value = Mock(status_code=200)
        message = EmailMultiAlternatives(
            subject='Password reset',
            body='Reset your password.',
            to=['resident@example.com'],
        )

        sent = message.send()

        self.assertEqual(sent, 1)
        post.assert_called_once_with(
            'https://api.resend.com/emails',
            json={
                'from': 'BantayPlaka <noreply@example.com>',
                'to': ['resident@example.com'],
                'subject': 'Password reset',
                'text': 'Reset your password.',
            },
            headers={'Authorization': 'Bearer re_test_key'},
            timeout=(5, 15),
        )

    @patch('apps.core.email_backend.requests.post')
    def test_sends_html_alternative(self, post):
        post.return_value = Mock(status_code=200)
        message = EmailMultiAlternatives(
            subject='Password reset',
            body='Reset your password.',
            to=['resident@example.com'],
        )
        message.attach_alternative('<p>Reset your password.</p>', 'text/html')

        ResendEmailBackend().send_messages([message])

        self.assertEqual(
            post.call_args.kwargs['json']['html'],
            '<p>Reset your password.</p>',
        )

    @override_settings(RESEND_API_KEY='')
    def test_requires_api_key(self):
        message = EmailMultiAlternatives(
            subject='Password reset',
            body='Reset your password.',
            to=['resident@example.com'],
        )

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            'RESEND_API_KEY must be configured',
        ):
            ResendEmailBackend().send_messages([message])

    @patch('apps.core.email_backend.requests.post')
    def test_surfaces_provider_errors(self, post):
        response = Mock(status_code=401)
        from requests import HTTPError

        response.raise_for_status.side_effect = HTTPError('Unauthorized')
        post.return_value = response
        message = EmailMultiAlternatives(
            subject='Password reset',
            body='Reset your password.',
            to=['resident@example.com'],
        )

        with self.assertRaises(HTTPError):
            ResendEmailBackend().send_messages([message])
