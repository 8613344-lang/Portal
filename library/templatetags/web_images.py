from django import template
from library.images import image_url

register = template.Library()
register.simple_tag(image_url, name='web_image')
