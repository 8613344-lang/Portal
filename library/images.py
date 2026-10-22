"""Private, bounded WebP derivatives; source files remain unchanged for exports."""
import hashlib
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_safe
from PIL import Image, ImageOps, UnidentifiedImageError

WIDTHS = (320, 640, 960, 1280, 1920)


def image_url(field, width=640):
    name = getattr(field, 'name', field)
    if not name: return ''
    width = next((size for size in WIDTHS if size >= int(width)), WIDTHS[-1])
    return reverse('web-image', kwargs={'width': width, 'path': name})


@require_safe
def web_image(request, width, path):
    from .views import authorized_upload
    if width not in WIDTHS or Path(path).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
        raise Http404
    source = authorized_upload(request, path)  # Also checked before a cached response or 304.
    try:
        stamp = source.storage.get_modified_time(source.name).isoformat()
        key = hashlib.sha256(f'webp-v1:{source.name}:{source.size}:{stamp}:{width}'.encode()).hexdigest()
        etag = '"'+key+'"'
        if request.headers.get('If-None-Match') == etag:
            response = HttpResponse(status=304)
        else:
            folder = Path(settings.MEDIA_ROOT) / '.web-images'
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = folder / (key+'.webp')
            if not target.exists():
                with source.open('rb') as stream, Image.open(stream) as original:
                    if original.width * original.height > 40_000_000: raise ValueError('Image too large')
                    image = ImageOps.exif_transpose(original)
                    image.thumbnail((width, width*2), Image.Resampling.LANCZOS)
                    image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=folder, suffix='.tmp', delete=False) as output:
                            temporary = Path(output.name)
                            image.save(output, format='WEBP', quality=90 if width >= 960 else 80, method=4)
                        os.replace(temporary, target)
                        temporary = None
                    finally:
                        image.close()
                        if temporary is not None: temporary.unlink(missing_ok=True)
            response = FileResponse(target.open('rb'), content_type='image/webp')
        response['ETag'] = etag
        response['Cache-Control'] = 'private, no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise Http404
