import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


class ResendEmailBackend(BaseEmailBackend):
    api_url = 'https://api.resend.com/emails'
    timeout = (5, 15)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        api_key = settings.RESEND_API_KEY
        if not api_key:
            raise ImproperlyConfigured(
                'RESEND_API_KEY must be configured to use the Resend email backend.'
            )

        sent_count = 0
        for message in email_messages:
            payload = {
                'from': message.from_email or settings.DEFAULT_FROM_EMAIL,
                'to': message.to,
                'subject': message.subject,
            }
            if message.cc:
                payload['cc'] = message.cc
            if message.bcc:
                payload['bcc'] = message.bcc
            if message.reply_to:
                payload['reply_to'] = message.reply_to

            if message.content_subtype == 'html':
                payload['html'] = message.body
            else:
                payload['text'] = message.body

            for content, mimetype in getattr(message, 'alternatives', []):
                if mimetype == 'text/html':
                    payload['html'] = content
                elif mimetype == 'text/plain':
                    payload['text'] = content

            if message.extra_headers:
                payload['headers'] = message.extra_headers

            if message.attachments:
                raise NotImplementedError(
                    'The Resend email backend does not support attachments.'
                )

            response = requests.post(
                self.api_url,
                json=payload,
                headers={'Authorization': f'Bearer {api_key}'},
                timeout=self.timeout,
            )
            if not response.ok:
                try:
                    error_data = response.json()
                except ValueError:
                    error_message = response.text[:1000]
                else:
                    error_message = (
                        error_data.get('message', '')
                        if isinstance(error_data, dict)
                        else ''
                    )
                raise requests.HTTPError(
                    (
                        f'Resend email request failed with HTTP '
                        f'{response.status_code}: {error_message or "No error details provided."}'
                    ),
                    response=response,
                )
            sent_count += 1

        return sent_count
