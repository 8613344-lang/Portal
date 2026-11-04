from django import forms
from django.contrib.auth.models import User
from .models import Order, Book
from .commerce import OCCASIONS, OTHER_OCCASION

class BookAdminForm(forms.ModelForm):
    source_pdf = forms.FileField(label='Загрузить книгу из PDF', required=False,
                                widget=forms.FileInput(attrs={'accept': '.pdf,application/pdf'}),
                                help_text='До 100 МБ и 100 листов. Только новая книга или книга без страниц. Листы сохраняются целиком; текст извлекается, если есть текстовый слой. Озвучка добавляется отдельно.')
    can_import_pdf = False
    class Meta:
        model = Book
        fields = '__all__'
        widgets = {name: forms.TextInput(attrs={'type': 'color'}) for name in ('ending_panel_color', 'ending_text_color', 'ending_qr_color')}
        widgets['ending_panel_transparency'] = forms.NumberInput(attrs={'min': 0, 'max': 100, 'step': 1})
    def clean_source_pdf(self):
        file = self.cleaned_data.get('source_pdf')
        if not file: return None
        if not self.can_import_pdf:
            raise forms.ValidationError('Нужно право добавления страниц книги.')
        if self.instance.pk and self.instance.pages.exists():
            raise forms.ValidationError('Импорт доступен только для книги без страниц. Создайте новую книгу.')
        if any(key.startswith('pages-') and key.endswith('-position') and value for key, value in self.data.items()):
            raise forms.ValidationError('Импорт PDF и ручное добавление страниц выполните отдельно.')
        from .pdf_import import prepare_pdf, PDFImportError
        try: return prepare_pdf(file)
        except PDFImportError as error: raise forms.ValidationError(str(error)) from error

class RegistrationForm(forms.Form):
    name = forms.CharField(label='Имя', max_length=150)
    email = forms.EmailField(label='Email', max_length=254)
    password1 = forms.CharField(label='Пароль', strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label='Повторите пароль', strip=False, widget=forms.PasswordInput)
    captcha = forms.CharField(label='Код с картинки', max_length=6)
    consent = forms.BooleanField(label='Согласен на обработку персональных данных')
    def clean_email(self):
        value = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise forms.ValidationError('Этот email уже используется. Войдите или восстановите пароль.')
        return value
    def clean(self):
        from django.contrib.auth.password_validation import validate_password
        data = super().clean()
        if data.get('password1') != data.get('password2'):
            self.add_error('password2','Пароли не совпадают.')
        elif data.get('password1'):
            validate_password(data['password1'], User(first_name=data.get('name',''),email=data.get('email','')))
        return data

class ChildBriefForm(forms.Form):
    child_name = forms.CharField(label='Имя ребёнка', max_length=100)
    child_age = forms.IntegerField(label='Возраст, лет', min_value=0, max_value=18)
    occasion = forms.ChoiceField(label='Повод для сказки', choices=[
        ('', 'Выберите повод'), *((occasion, occasion) for occasion in OCCASIONS), (OTHER_OCCASION, 'Свой повод'),
    ])
    event = forms.CharField(label='Ваш повод (если выбрали «Свой повод»)', max_length=300, required=False)

    def clean(self):
        data = super().clean()
        if data.get('occasion') == OTHER_OCCASION and not data.get('event'):
            self.add_error('event', 'Напишите пару слов о вашем поводе.')
        return data

    @property
    def event_name(self):
        return self.cleaned_data['event'] if self.cleaned_data['occasion'] == OTHER_OCCASION else self.cleaned_data['occasion']


class OrderForm(forms.ModelForm):
    consent = forms.BooleanField(label='Я согласен(на) на обработку данных для выполнения заказа')
    website = forms.CharField(required=False, widget=forms.HiddenInput)
    tariff = forms.ChoiceField(label='Тариф', required=False)
    narration = forms.BooleanField(label='Добавить озвучку', required=False)
    printed = forms.BooleanField(label='Добавить печатный экземпляр в мягкой обложке', required=False)
    hardcover = forms.BooleanField(label='Обсудить твёрдый переплёт', required=False)

    def __init__(self, *args, tariffs, **kwargs):
        super().__init__(*args, **kwargs)
        self.tariffs = {str(tariff.pk): tariff for tariff in tariffs}
        self.fields['tariff'].choices = [
            ('', 'Помогите выбрать'), *((key, f'{tariff.title} · {tariff.price} ₽') for key, tariff in self.tariffs.items()),
        ]

    class Meta:
        model = Order
        fields = ['parent_name', 'email', 'contact', 'child_description', 'wishes', 'photo', 'consent']
        widgets = {name: forms.Textarea(attrs={'rows': 3}) for name in ['child_description', 'wishes']}
        widgets['parent_name'] = forms.TextInput(attrs={'autocomplete': 'name'})
        widgets['email'] = forms.EmailInput(attrs={'autocomplete': 'email'})
        widgets['photo'] = forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'})
        labels = {'child_description': 'Характер, увлечения или любимые герои'}
        help_texts = {'photo': 'JPEG, PNG или WebP до 10 МБ. Фото доступно только сотрудникам.'}

    def pricing_snapshot(self):
        data = self.cleaned_data
        tariff = self.tariffs.get(data.get('tariff'))
        result = {'твёрдый переплёт: обсудить': data['hardcover']}
        if tariff:
            result |= {'тариф': tariff.title, 'база': tariff.price}
            if tariff.book_count:
                result |= {'книг': tariff.book_count, 'месяцев': tariff.months}
            elif tariff.total_pages:
                result |= {'страниц': tariff.total_pages}
        for field, name, price in [
            ('narration', 'озвучка', tariff.audio_price if tariff else None),
            ('printed', 'мягкая обложка', tariff.print_price if tariff else None),
        ]:
            if data[field]:
                result[name] = price if price is not None else 'обсудить'
        if tariff and all(isinstance(result.get(name, 0), int) for name in ('озвучка', 'мягкая обложка')):
            result['итого без доставки и твёрдого переплёта'] = tariff.price + result.get('озвучка', 0) + result.get('мягкая обложка', 0)
        return result

    def clean_website(self):
        if self.cleaned_data.get('website'):
            raise forms.ValidationError('Не удалось отправить форму.')
        return ''

class PageAudioForm(forms.Form):
    from .validators import validate_audio
    audio = forms.FileField(label='Запись MP3 / WAV', validators=[validate_audio], widget=forms.FileInput(attrs={'accept': '.mp3,.wav'}))
