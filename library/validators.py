from io import BytesIO
from pathlib import Path
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError
from mutagen import File as AudioFile

def validate_audio(value):
    if not value:
        return
    if Path(value.name).suffix.lower() not in {'.mp3', '.wav'} or not 0 < value.size <= 100 * 1024 * 1024:
        raise ValidationError('Выберите MP3 или WAV размером до 100 МБ.')
    try:
        value.seek(0)
        sound = AudioFile(value)
        if sound is None or sound.info.length <= 0:
            raise ValueError()
        from mutagen.mp3 import MP3
        from mutagen.wave import WAVE
        expected = MP3 if Path(value.name).suffix.lower() == '.mp3' else WAVE
        if not isinstance(sound, expected):
            raise ValueError()
    except Exception as exc:
        raise ValidationError('Не удалось прочитать аудиофайл. Нужна настоящая запись MP3 или WAV.') from exc
    finally:
        value.seek(0)

def normalize_image(value):
    if not value or getattr(value, '_committed', False):
        return value
    if not 0 < value.size <= 10 * 1024 * 1024:
        raise ValidationError('Изображение должно быть не больше 10 МБ.')
    try:
        value.seek(0)
        image = Image.open(value)
        if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 25_000_000:
            raise ValueError()
        image.load()
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((2400, 2400))
        output = BytesIO()
        image.save(output, format='JPEG', quality=90)
        return SimpleUploadedFile('image.jpg', output.getvalue(), content_type='image/jpeg')
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValidationError('Загрузите JPEG, PNG или WebP до 25 мегапикселей.') from exc
    finally:
        value.seek(0)
