import time
from pathlib import PurePosixPath

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateRegistrationDocumentStorage(Storage):
    def __init__(self):
        self._local_storage = FileSystemStorage(
            location=settings.PRIVATE_MEDIA_ROOT,
            base_url=None,
        )

    def _cloudinary_enabled(self):
        if not settings.USE_CLOUDINARY:
            if not settings.DEBUG:
                raise ImproperlyConfigured(
                    'Production registration documents require private Cloudinary storage.'
                )
            return False
        if not all((
            settings.CLOUDINARY_CLOUD_NAME,
            settings.CLOUDINARY_API_KEY,
            settings.CLOUDINARY_API_SECRET,
        )):
            raise ImproperlyConfigured(
                'Private registration document storage requires all Cloudinary credentials.'
            )
        return True

    @staticmethod
    def _cloudinary_parts(name):
        path = PurePosixPath(name.replace('\\', '/'))
        file_format = path.suffix.lower().lstrip('.')
        if file_format not in {'jpg', 'jpeg', 'jpe', 'png', 'pdf'}:
            raise ValueError(f'Unsupported registration document format: {file_format or "none"}')
        cloudinary_format = 'jpg' if file_format in {'jpeg', 'jpe'} else file_format
        return str(path.with_suffix('')), cloudinary_format

    def _save(self, name, content):
        name = name.replace('\\', '/')
        if not self._cloudinary_enabled():
            return self._local_storage.save(name, content)

        import cloudinary.uploader

        public_id, _file_format = self._cloudinary_parts(name)
        result = cloudinary.uploader.upload(
            content,
            public_id=public_id,
            resource_type='image',
            type='authenticated',
            unique_filename=False,
            use_filename=False,
            overwrite=True,
        )
        if result.get('type') != 'authenticated' or result.get('resource_type') != 'image':
            cloudinary.uploader.destroy(
                result.get('public_id', public_id),
                resource_type=result.get('resource_type', 'image'),
                type=result.get('type', 'upload'),
                invalidate=True,
            )
            raise IOError('Cloudinary did not store the registration document as an authenticated image.')
        return name

    def _open(self, name, mode='rb'):
        if not self._cloudinary_enabled():
            return self._local_storage.open(name, mode)

        import cloudinary.api
        from cloudinary.utils import private_download_url

        public_id, file_format = self._cloudinary_parts(name)
        cloudinary.api.resource(
            public_id,
            resource_type='image',
            type='authenticated',
        )
        url = private_download_url(
            public_id,
            file_format,
            resource_type='image',
            type='authenticated',
            expires_at=int(time.time()) + 300,
        )
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            raise FileNotFoundError(name)
        response.raise_for_status()
        document = ContentFile(response.content, name=name)
        document.mode = mode
        return document

    def delete(self, name):
        if not self._cloudinary_enabled():
            return self._local_storage.delete(name)

        import cloudinary.uploader

        public_id, _file_format = self._cloudinary_parts(name)
        result = cloudinary.uploader.destroy(
            public_id,
            resource_type='image',
            type='authenticated',
            invalidate=True,
        )
        return result.get('result') == 'ok'

    def exists(self, name):
        if not self._cloudinary_enabled():
            return self._local_storage.exists(name)

        import cloudinary.api
        import cloudinary.exceptions

        public_id, _file_format = self._cloudinary_parts(name)
        try:
            cloudinary.api.resource(
                public_id,
                resource_type='image',
                type='authenticated',
            )
        except cloudinary.exceptions.NotFound:
            return False
        return True

    def size(self, name):
        if not self._cloudinary_enabled():
            return self._local_storage.size(name)

        import cloudinary.api

        public_id, _file_format = self._cloudinary_parts(name)
        resource = cloudinary.api.resource(
            public_id,
            resource_type='image',
            type='authenticated',
        )
        return resource['bytes']

    def url(self, name):
        if not self._cloudinary_enabled():
            raise ValueError('Private registration documents are served through authenticated application views.')

        from cloudinary.utils import private_download_url

        public_id, file_format = self._cloudinary_parts(name)
        return private_download_url(
            public_id,
            file_format,
            resource_type='image',
            type='authenticated',
            expires_at=int(time.time()) + 300,
        )

    def path(self, name):
        if self._cloudinary_enabled():
            raise NotImplementedError('Cloudinary documents do not have a local filesystem path.')
        return self._local_storage.path(name)
