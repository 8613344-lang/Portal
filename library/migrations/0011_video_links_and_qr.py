import uuid
from django.db import migrations, models


def populate_video_tokens(apps, schema_editor):
    Book = apps.get_model('library', 'Book')
    for pk in Book.objects.using(schema_editor.connection.alias).filter(video_share_token__isnull=True).values_list('pk', flat=True).iterator():
        Book.objects.using(schema_editor.connection.alias).filter(pk=pk).update(video_share_token=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [('library', '0010_bookvideo')]
    operations = [
        migrations.AddField(model_name='book', name='video_share_token',
            field=models.UUIDField(null=True, unique=True, editable=False)),
        migrations.RunPython(populate_video_tokens, migrations.RunPython.noop),
        migrations.AlterField(model_name='book', name='video_share_token',
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
        migrations.AlterField(model_name='book', name='ending_show_qr',
            field=models.BooleanField('QR-код книги на последней странице', default=True)),
        migrations.AddField(model_name='book', name='ending_show_video_qr',
            field=models.BooleanField('QR-код видео на последней странице', default=False,
                help_text='Ссылка открывает видео без регистрации. После настройки QR создайте или пересоздайте MP4 в разделе «Видео». Можно включить оба QR-кода.')),
    ]
