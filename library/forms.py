from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Order, Book

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

class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label='Email')
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['username', 'email', 'password1', 'password2']

class OrderForm(forms.ModelForm):
    consent = forms.BooleanField(label='Я согласен(на) на обработку данных для выполнения заказа')
    website = forms.CharField(required=False, widget=forms.HiddenInput)
    class Meta:
        model = Order
        fields = ['parent_name', 'email', 'contact', 'child_name', 'child_age', 'child_description', 'event', 'wishes', 'photo', 'consent']
        widgets = {name: forms.Textarea(attrs={'rows': 3}) for name in ['child_description', 'event', 'wishes']}
        widgets['photo'] = forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'})
        labels = {'child_description': 'Расскажите о ребёнке: характер, увлечения, любимые герои', 'event': 'Какое событие или тему осветить в книге?'}
        help_texts = {'photo': 'Необязательно. JPEG, PNG или WebP, до 10 МБ. Фото доступно только сотрудникам.', 'child_age': 'От 0 до 18 лет.'}
    def clean_child_age(self):
        value = self.cleaned_data['child_age']
        if value > 18:
            raise forms.ValidationError('Укажите возраст от 0 до 18 лет.')
        return value
    def clean_website(self):
        if self.cleaned_data.get('website'):
            raise forms.ValidationError('Не удалось отправить форму.')
        return ''

class PageAudioForm(forms.Form):
    from .validators import validate_audio
    audio = forms.FileField(label='Запись MP3 / WAV', validators=[validate_audio], widget=forms.FileInput(attrs={'accept': '.mp3,.wav'}))
