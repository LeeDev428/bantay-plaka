from django.conf import settings
from django.core.files.storage import Storage, default_storage
from django.utils.deconstruct import deconstructible


@deconstructible
class VideoMediaStorage(Storage):
    @property
    def backend(self):
        cloudinary_ready = (
            getattr(settings, 'USE_CLOUDINARY', False)
            and getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
            and getattr(settings, 'CLOUDINARY_API_KEY', '')
            and getattr(settings, 'CLOUDINARY_API_SECRET', '')
        )
        if cloudinary_ready:
            from cloudinary_storage.storage import VideoMediaCloudinaryStorage

            return VideoMediaCloudinaryStorage()
        return default_storage

    def _open(self, name, mode='rb'):
        return self.backend.open(name, mode)

    def _save(self, name, content):
        return self.backend.save(name, content)

    def delete(self, name):
        return self.backend.delete(name)

    def exists(self, name):
        return self.backend.exists(name)

    def listdir(self, path):
        return self.backend.listdir(path)

    def size(self, name):
        return self.backend.size(name)

    def url(self, name):
        backend = self.backend
        from cloudinary_storage.storage import VideoMediaCloudinaryStorage

        if not isinstance(backend, VideoMediaCloudinaryStorage):
            return backend.url(name)

        import cloudinary.utils

        return cloudinary.utils.cloudinary_url(
            backend._prepend_prefix(name),
            resource_type='video',
            secure=True,
            format='mp4',
            video_codec='h264',
        )[0]
