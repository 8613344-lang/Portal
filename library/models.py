import uuid
from pathlib import Path
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from .validators import normalize_image, validate_audio

def private_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    return f'{instance._meta.model_name}/{uuid.uuid4().hex}{suffix}'

class BookQuerySet(models.QuerySet):
    def visible_to(self, user):
        if user.is_authenticated and user.is_staff and user.has_perm('library.view_book'):
            return self
        query = Q(status='published', visibility='public')
        if user.is_authenticated:
            query |= Q(status='published', grants__user=user)
        return self.filter(query).distinct()

class Book(models.Model):
    title = models.CharField('Название', max_length=200)
    slug = models.SlugField('Адрес', max_length=120, unique=True)
    description = models.TextField('Описание', blank=True)
    child_name = models.CharField('Имя героя', max_length=100, blank=True)
    age_label = models.CharField('Возраст читателей', max_length=60, default='Для детей от 2 до 5 лет')
    cover = models.FileField('Обложка', upload_to=private_path, blank=True)
    cover_full_page = models.BooleanField('Обложка целиком из картинки', default=False)
    cover_page = models.ForeignKey('BookPage', verbose_name='Лист книги для обложки', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    cover_audio = models.FileField('Озвучка обложки MP3 / WAV', upload_to=private_path, blank=True, validators=[validate_audio])
    ending_image = models.FileField('Картинка последней страницы', upload_to=private_path, blank=True)
    ending_full_page = models.BooleanField('Последняя страница целиком из картинки', default=False)
    ending_page = models.ForeignKey('BookPage', verbose_name='Лист книги для последней страницы', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    ending_audio = models.FileField('Озвучка последней страницы MP3 / WAV', upload_to=private_path, blank=True, validators=[validate_audio])
    ending_show_qr = models.BooleanField('Показывать QR-код на последней странице', default=True)
    ending_show_text = models.BooleanField('Показывать текст на последней странице', default=True)
    ending_title = models.CharField('Заголовок последней страницы', max_length=200, blank=True, default='Продолжение начинается здесь')
    ending_text = models.TextField('Текст последней страницы', blank=True, default='Новые истории и персональные книги для вашего ребёнка.')
    ending_panel_color = models.CharField('Цвет подложки текста и QR', max_length=7, default='#ffffff', validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Укажите цвет в формате #RRGGBB.')])
    ending_panel_transparency = models.PositiveSmallIntegerField('Прозрачность подложки, %', default=7, validators=[MinValueValidator(0), MaxValueValidator(100)], help_text='0 — непрозрачная подложка; 100 — полностью прозрачная.')
    ending_text_color = models.CharField('Цвет текста последней страницы', max_length=7, default='#304b40', validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Укажите цвет в формате #RRGGBB.')])
    ending_qr_color = models.CharField('Цвет QR-кода', max_length=7, default='#000000', validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Укажите цвет в формате #RRGGBB.')])
    pdf_layout = models.CharField('Вариант выгрузки PDF', max_length=20, default='combined', choices=[('combined', 'Картинка и текст на одном листе'), ('alternating', 'Полный лист картинки, затем лист текста')])
    background_theme = models.CharField('Фон книги', max_length=20, default='paper', choices=[('paper', 'Тёплая бумага'), ('white', 'Белый'), ('mint', 'Мятный'), ('sky', 'Небесный'), ('rose', 'Розовый'), ('custom', 'Своя картинка')])
    background_image = models.FileField('Своя картинка фона', upload_to=private_path, blank=True, help_text='JPEG, PNG или WebP; фон используется в просмотрщике и PDF.')
    status = models.CharField('Состояние', max_length=20, choices=[('draft', 'Черновик'), ('published', 'Опубликована'), ('archived', 'В архиве')], default='draft', db_index=True)
    visibility = models.CharField('Доступ', max_length=20, choices=[('public', 'Публичная'), ('restricted', 'Для выбранных пользователей')], default='restricted', db_index=True)
    created_at = models.DateTimeField('Создана', auto_now_add=True)
    objects = BookQuerySet.as_manager()
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Книга'
        verbose_name_plural = 'Книги'
    def __str__(self): return self.title
    @property
    def ending_panel_css(self):
        value = self.ending_panel_color.lstrip('#')
        rgb = ','.join(str(int(value[i:i+2], 16)) for i in (0, 2, 4))
        return f'rgba({rgb},{(100-self.ending_panel_transparency)/100:.2f})'
    def clean(self):
        from django.core.exceptions import ValidationError
        if self.background_image and not self.background_image._committed:
            self.background_theme = 'custom'
        self.cover = normalize_image(self.cover)
        self.background_image = normalize_image(self.background_image)
        self.ending_image = normalize_image(self.ending_image)
        for field in ('cover_page', 'ending_page'):
            page = getattr(self, field)
            if page and page.book_id != self.pk:
                raise ValidationError({field: 'Выберите страницу этой книги.'})
            if page and not page.illustration:
                raise ValidationError({field: 'Выбранный лист должен содержать картинку.'})
        if self.cover_page_id and self.cover_page_id == self.ending_page_id:
            raise ValidationError({'ending_page': 'Обложка и последняя страница должны быть разными листами.'})
        if self.cover_full_page and not (self.cover or self.cover_page_id):
            raise ValidationError({'cover': 'Загрузите обложку или выберите лист книги.'})
        if self.ending_full_page and not (self.ending_image or self.ending_page_id):
            raise ValidationError({'ending_image': 'Загрузите финальную картинку или выберите лист книги.'})
        if self.background_theme == 'custom' and not self.background_image:
            raise ValidationError({'background_image': 'Для своего фона загрузите картинку.'})

class BookPage(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='pages', verbose_name='Книга')
    position = models.PositiveIntegerField('Номер страницы')
    title = models.CharField('Заголовок', max_length=200)
    text = models.TextField('Текст', blank=True, help_text='Абзацы разделяйте пустой строкой. HTML не поддерживается.')
    illustration = models.FileField('Иллюстрация', upload_to=private_path, blank=True)
    audio = models.FileField('Озвучка MP3 / WAV', upload_to=private_path, blank=True, validators=[validate_audio])
    pdf_full_page = models.BooleanField('Лист PDF целиком', default=False, help_text='Импортированный лист показывается и выгружается целиком, без повторного заголовка или текста поверх изображения.')
    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['book', 'position'], name='unique_book_page')]
        verbose_name = 'Страница книги'
        verbose_name_plural = 'Страницы книг'
    def __str__(self): return f'{self.book}: {self.position}. {self.title}'
    def clean(self): self.illustration = normalize_image(self.illustration)

class BookAccess(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='grants', verbose_name='Книга')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='book_grants', verbose_name='Читатель')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['book', 'user'], name='unique_book_access')]
        verbose_name = 'Доступ к книге'
        verbose_name_plural = 'Доступы к книгам'
    def __str__(self): return f'{self.user} → {self.book}'

class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission_key = models.UUIDField(unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Заказчик')
    parent_name = models.CharField('Имя родителя', max_length=120)
    email = models.EmailField('Email')
    contact = models.CharField('Дополнительная связь', max_length=160, blank=True)
    child_name = models.CharField('Имя ребёнка', max_length=100)
    child_age = models.PositiveSmallIntegerField('Возраст')
    child_description = models.TextField('О ребёнке')
    event = models.TextField('Событие / тема')
    wishes = models.TextField('Пожелания', blank=True)
    photo = models.FileField('Фото ребёнка', upload_to=private_path, blank=True)
    consent = models.BooleanField('Согласие на обработку', default=False)
    status = models.CharField('Статус', max_length=20, choices=[('new', 'Новая'), ('in_progress', 'В работе'), ('review', 'На согласовании'), ('ready', 'Готова'), ('completed', 'Завершена'), ('cancelled', 'Отменена')], default='new', db_index=True)
    book = models.ForeignKey(Book, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Готовая книга')
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='managed_orders', verbose_name='Менеджер', limit_choices_to={'is_staff': True})
    created_at = models.DateTimeField('Получена', auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
    def __str__(self): return f'Заказ {str(self.id)[:8]} — {self.parent_name}'
    def clean(self): self.photo = normalize_image(self.photo)

class NotificationRecipient(models.Model):
    name = models.CharField('Имя получателя', max_length=100)
    channel = models.CharField('Канал', max_length=12, choices=[('email', 'Email'), ('telegram', 'Telegram')])
    destination = models.CharField('Email или Telegram user ID', max_length=254)
    active = models.BooleanField('Активен', default=True)
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, verbose_name='Сотрудник', limit_choices_to={'is_staff': True}, help_text='Обязательно для Telegram: нужен активный сотрудник с правом просмотра заказов.')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['channel', 'destination'], name='unique_recipient')]
        verbose_name = 'Получатель уведомлений'
        verbose_name_plural = 'Получатели уведомлений'
    def __str__(self): return self.name
    def clean(self):
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email
        if self.channel == 'email':
            validate_email(self.destination)
        elif self.channel == 'telegram':
            if not self.destination.isdecimal() or int(self.destination) <= 0:
                raise ValidationError({'destination': 'Нужен положительный числовой Telegram user ID, не username.'})
            if not self.employee_id or not self.employee.is_staff or not self.employee.is_active or not self.employee.has_perm('library.view_order'):
                raise ValidationError({'employee': 'Нужен активный сотрудник с правом просмотра заказов.'})
    def allowed(self):
        return self.active and (self.channel == 'email' or bool(self.employee_id and self.employee.is_active and self.employee.is_staff and self.employee.has_perm('library.view_order')))

class Notification(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='notifications', verbose_name='Заказ')
    recipient = models.ForeignKey(NotificationRecipient, on_delete=models.PROTECT, verbose_name='Получатель')
    destination = models.CharField(max_length=254)
    channel = models.CharField(max_length=12)
    state = models.CharField('Состояние', max_length=12, choices=[('pending', 'В очереди'), ('sending', 'Отправляется'), ('sent', 'Отправлено'), ('failed', 'Ошибка'), ('cancelled', 'Отменено')], default='pending', db_index=True)
    attempts = models.PositiveIntegerField('Попыток', default=0)
    next_attempt = models.DateTimeField('Следующая попытка', default=timezone.now, db_index=True)
    lease_token = models.UUIDField(null=True, blank=True)
    last_error = models.CharField('Ошибка', max_length=250, blank=True)
    sent_at = models.DateTimeField('Отправлено', null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'recipient'], name='unique_order_notification')]
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'

class BotState(models.Model):
    name = models.CharField(max_length=40, primary_key=True)
    offset = models.BigIntegerField(default=0)

class RateEvent(models.Model):
    key = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
