from pathlib import PurePosixPath

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.residents.models import Resident, Vehicle


class Command(BaseCommand):
    help = 'Move existing resident ID and vehicle documents into private storage.'

    def handle(self, *args, **options):
        if not settings.USE_CLOUDINARY and not settings.DEBUG:
            raise CommandError('Production registration documents require private Cloudinary storage.')
        if settings.USE_CLOUDINARY and not all((
            settings.CLOUDINARY_CLOUD_NAME,
            settings.CLOUDINARY_API_KEY,
            settings.CLOUDINARY_API_SECRET,
        )):
            raise CommandError('Private Cloudinary document migration needs all Cloudinary credentials.')

        migrated = 0
        for model, fields in (
            (Resident, ('valid_id_image',)),
            (Vehicle, ('or_document', 'cr_document')),
        ):
            records = model.objects.all().only('pk', *fields).iterator()
            for record in records:
                for field_name in fields:
                    if self._migrate_document(record, field_name):
                        migrated += 1

        self.stdout.write(self.style.SUCCESS(f'Private document migration complete ({migrated} migrated).'))

    def _migrate_document(self, record, field_name):
        document = getattr(record, field_name)
        old_name = document.name
        if not old_name:
            return False

        storage = document.storage
        file_format = PurePosixPath(old_name).suffix.lower().lstrip('.')
        content = None
        if not file_format:
            content, file_format = self._read_legacy_document(old_name)
        new_name = old_name if PurePosixPath(old_name).suffix else f'{old_name}.{file_format}'

        already_private = storage.exists(new_name)
        if not already_private:
            if content is None:
                content, detected_format = self._read_legacy_document(old_name)
                if file_format and self._normalize_format(detected_format) != self._normalize_format(file_format):
                    raise CommandError(f'File type changed while migrating document {record.pk}:{field_name}.')
                file_format = detected_format
                if not PurePosixPath(old_name).suffix:
                    new_name = f'{old_name}.{file_format}'
            document.save(
                new_name,
                ContentFile(content, name=PurePosixPath(new_name).name),
                save=False,
            )
            new_name = document.name
            if not storage.exists(new_name):
                raise CommandError(f'Private copy was not found after saving {record.pk}:{field_name}.')

        if document.name != new_name:
            document.name = new_name
        if document.name != old_name:
            record.save(update_fields=[field_name])

        self._delete_legacy_document(old_name)
        return not already_private or document.name != old_name

    def _read_legacy_document(self, name):
        if settings.USE_CLOUDINARY:
            import cloudinary.api
            from cloudinary.utils import cloudinary_url

            public_id = str(PurePosixPath(name).with_suffix())
            resource = cloudinary.api.resource(
                public_id,
                resource_type='image',
                type='upload',
            )
            file_format = self._normalize_format(str(resource['format']).lower())
            url, _options = cloudinary_url(
                public_id,
                resource_type='image',
                type='upload',
                format=file_format,
                secure=True,
                sign_url=False,
            )
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.content, file_format

        with default_storage.open(name, 'rb') as source:
            content = source.read()
        file_format = PurePosixPath(name).suffix.lower().lstrip('.')
        if file_format:
            return content, file_format
        if content.startswith(b'%PDF-'):
            return content, 'pdf'
        if content.startswith(b'\x89PNG\r\n\x1a\n'):
            return content, 'png'
        if content.startswith(b'\xff\xd8\xff'):
            return content, 'jpg'
        raise CommandError(f'Could not determine the file type for document {name}.')

    @staticmethod
    def _normalize_format(file_format):
        return 'jpg' if file_format in {'jpeg', 'jpe'} else file_format

    def _delete_legacy_document(self, name):
        if settings.USE_CLOUDINARY:
            import cloudinary.api
            import cloudinary.exceptions
            import cloudinary.uploader

            public_id = str(PurePosixPath(name).with_suffix())
            try:
                cloudinary.api.resource(
                    public_id,
                    resource_type='image',
                    type='upload',
                )
            except cloudinary.exceptions.NotFound:
                return
            result = cloudinary.uploader.destroy(
                public_id,
                resource_type='image',
                type='upload',
                invalidate=True,
            )
            if result.get('result') not in {'ok', 'not found'}:
                raise CommandError(f'Could not remove public Cloudinary document {name}.')
            return

        if default_storage.exists(name):
            default_storage.delete(name)
        if default_storage.exists(name):
            raise CommandError(f'Could not remove public media document {name}.')
