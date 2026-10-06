from django.core.files.storage import FileSystemStorage
from django.urls import reverse
from whitenoise.storage import CompressedManifestStaticFilesStorage

class PrivateStorage(FileSystemStorage):
    def url(self, name):
        return reverse('private-file', kwargs={'path': name})

class PublicStaticStorage(CompressedManifestStaticFilesStorage):
    def __init__(self, *args, **kwargs):
        # Static assets are public; private upload permissions must not apply.
        kwargs['file_permissions_mode'] = 0o644
        kwargs['directory_permissions_mode'] = 0o755
        super().__init__(*args, **kwargs)
