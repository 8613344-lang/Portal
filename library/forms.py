from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Order

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
