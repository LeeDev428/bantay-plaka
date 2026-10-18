from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from types import SimpleNamespace
from unittest.mock import patch

from apps.accounts.forms import ResidentSignupForm
from apps.residents.forms import ResidentForm, VehicleForm, VehicleRegistrationFormSetFactory
from apps.residents.storage import PrivateRegistrationDocumentStorage
from apps.residents.validators import MAX_DOCUMENT_SIZE, validate_uploaded_document
from apps.residents.views import resident_id_document


class RegistrationDocumentValidationTests(SimpleTestCase):
    def test_accepts_supported_pdf_and_image_signatures(self):
        documents = (
            SimpleUploadedFile('id.pdf', b'%PDF-1.7 document'),
            SimpleUploadedFile('id.jpg', b'\xff\xd8\xff\x00document'),
            SimpleUploadedFile('id.png', b'\x89PNG\r\n\x1a\n document'),
        )
        for document in documents:
            with self.subTest(filename=document.name):
                validate_uploaded_document(document)

    def test_rejects_mismatched_file_content(self):
        document = SimpleUploadedFile('document.pdf', b'not a pdf')
        with self.assertRaises(ValidationError):
            validate_uploaded_document(document)

    def test_rejects_unsupported_extension_and_files_over_five_megabytes(self):
        unsupported = SimpleUploadedFile('document.gif', b'GIF89a')
        oversized = SimpleUploadedFile('document.pdf', b'%PDF-' + b'x' * MAX_DOCUMENT_SIZE)

        with self.assertRaises(ValidationError):
            validate_uploaded_document(unsupported)
        with self.assertRaises(ValidationError):
            validate_uploaded_document(oversized)


class ResidentRegistrationWizardTests(SimpleTestCase):
    def test_resident_edit_upload_widget_does_not_render_public_storage_urls(self):
        form = ResidentForm()

        self.assertIsInstance(form.fields['valid_id_image'].widget, forms.FileInput)

    def test_signup_fields_do_not_ask_for_username_and_require_consent(self):
        form = ResidentSignupForm()

        self.assertNotIn('username', form.fields)
        self.assertIn('telephone_number', form.fields)
        self.assertFalse(form.fields['telephone_number'].required)
        self.assertTrue(form.fields['privacy_consent'].required)
        self.assertTrue(form.fields['valid_id_image'].required)

    def test_vehicle_documents_are_required_when_a_vehicle_is_added(self):
        form = VehicleForm()

        self.assertTrue(form.fields['or_document'].required)
        self.assertTrue(form.fields['cr_document'].required)

    def test_registration_allows_zero_vehicles(self):
        formset = VehicleRegistrationFormSetFactory(
            data={
                'vehicles-TOTAL_FORMS': '0',
                'vehicles-INITIAL_FORMS': '0',
                'vehicles-MIN_NUM_FORMS': '0',
                'vehicles-MAX_NUM_FORMS': '1000',
            },
            prefix='vehicles',
        )

        self.assertTrue(formset.is_valid())
        self.assertEqual(formset.forms, [])

    @override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
    def test_registration_page_renders_three_steps_and_privacy_modal(self):
        response = Client().get('/resident/register/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Account &amp; Personal')
        self.assertContains(response, 'Contact &amp; Address')
        self.assertContains(response, 'Vehicles &amp; Consent')
        self.assertContains(response, 'privacy-notice-modal')
        self.assertContains(response, 'https://privacy.gov.ph/data-privacy-act/')


class PrivateRegistrationDocumentStorageTests(SimpleTestCase):
    @override_settings(
        DEBUG=False,
        USE_CLOUDINARY=True,
        CLOUDINARY_CLOUD_NAME='test-cloud',
        CLOUDINARY_API_KEY='test-key',
        CLOUDINARY_API_SECRET='test-secret',
    )
    @patch('cloudinary.uploader.upload')
    def test_uploads_documents_as_authenticated_cloudinary_assets(self, upload):
        upload.return_value = {
            'public_id': 'resident_ids/test-document',
            'resource_type': 'image',
            'type': 'authenticated',
        }
        storage = PrivateRegistrationDocumentStorage()

        saved_name = storage._save('resident_ids/test-document.pdf', b'%PDF-1.7')

        self.assertEqual(saved_name, 'resident_ids/test-document.pdf')
        self.assertEqual(upload.call_args.kwargs['type'], 'authenticated')
        self.assertEqual(upload.call_args.kwargs['resource_type'], 'image')

    @override_settings(
        DEBUG=False,
        USE_CLOUDINARY=True,
        CLOUDINARY_CLOUD_NAME='test-cloud',
        CLOUDINARY_API_KEY='test-key',
        CLOUDINARY_API_SECRET='test-secret',
    )
    @patch('cloudinary.utils.private_download_url', return_value='https://cloudinary.test/private')
    def test_document_urls_are_signed_authenticated_downloads(self, private_url):
        storage = PrivateRegistrationDocumentStorage()

        url = storage.url('vehicle_documents/or/test-document.jpg')

        self.assertEqual(url, 'https://cloudinary.test/private')
        self.assertEqual(private_url.call_args.args, ('vehicle_documents/or/test-document', 'jpg'))
        self.assertEqual(private_url.call_args.kwargs['type'], 'authenticated')

    @override_settings(
        DEBUG=False,
        USE_CLOUDINARY=True,
        CLOUDINARY_CLOUD_NAME='',
        CLOUDINARY_API_KEY='',
        CLOUDINARY_API_SECRET='',
    )
    def test_production_fails_closed_without_cloudinary_credentials(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'requires all Cloudinary credentials'):
            PrivateRegistrationDocumentStorage().exists('resident_ids/test-document.pdf')

    @override_settings(DEBUG=True, USE_CLOUDINARY=False)
    def test_local_storage_does_not_expose_a_public_url(self):
        with self.assertRaisesMessage(ValueError, 'authenticated application views'):
            PrivateRegistrationDocumentStorage().url('resident_ids/test-document.pdf')


class RegistrationDocumentAccessTests(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()
        self.document = SimpleNamespace(
            name='resident_ids/test-document.pdf',
            open=lambda _mode: ContentFile(b'%PDF-1.7'),
        )
        self.resident = SimpleNamespace(user_id=7, valid_id_image=self.document)

    @patch('apps.residents.views.get_object_or_404')
    def test_resident_can_open_their_own_id_document(self, get_object):
        get_object.return_value = self.resident
        request = self.request_factory.get('/residents/7/id-document/')
        request.user = SimpleNamespace(
            pk=7,
            is_authenticated=True,
            is_admin=lambda: False,
            is_resident=lambda: True,
        )

        response = resident_id_document(request, 7)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        response.close()

    @patch('apps.residents.views.get_object_or_404')
    def test_other_residents_cannot_open_the_document(self, get_object):
        get_object.return_value = self.resident
        request = self.request_factory.get('/residents/7/id-document/')
        request.user = SimpleNamespace(
            pk=8,
            is_authenticated=True,
            is_admin=lambda: False,
            is_resident=lambda: True,
        )

        response = resident_id_document(request, 7)

        self.assertEqual(response.status_code, 403)
