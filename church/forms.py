from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.files.base import ContentFile
from io import BytesIO
from PIL import Image, ImageOps

from .models import FeedPost


ALLOWED_FEED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
MAX_FEED_IMAGE_SIZE = 2000


class FeedPostForm(forms.ModelForm):
    class Meta:
        model = FeedPost
        fields = ("body",)
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Share an update, a few photos, or a useful link...",
                }
            )
        }
        labels = {"body": ""}


def validate_feed_image_upload(upload):
    extension = upload.name.rsplit(".", 1)[-1].lower()
    if upload.content_type not in ALLOWED_FEED_IMAGE_TYPES or extension not in {"jpg", "jpeg", "png", "webp"}:
        raise forms.ValidationError("Photos must be JPG, PNG, or WebP files.")


def prepare_feed_image_upload(upload):
    validate_feed_image_upload(upload)
    try:
        with Image.open(upload) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((MAX_FEED_IMAGE_SIZE, MAX_FEED_IMAGE_SIZE), Image.Resampling.LANCZOS)
            extension = upload.name.rsplit(".", 1)[-1].lower()
            if extension in {"jpg", "jpeg"}:
                image_format = "JPEG"
                content_type = "image/jpeg"
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                save_kwargs = {"quality": 86}
            elif extension == "png":
                image_format = "PNG"
                content_type = "image/png"
                save_kwargs = {}
            else:
                image_format = "WEBP"
                content_type = "image/webp"
                save_kwargs = {"quality": 86}
            output = BytesIO()
            image.save(output, format=image_format, **save_kwargs)
    except OSError:
        raise forms.ValidationError("One of the selected photos could not be read.")

    output.seek(0)
    processed = ContentFile(output.read())
    processed.name = upload.name
    processed.content_type = content_type
    return processed


class PublicRegistrationForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name")
    email = forms.EmailField(label="Email")

    class Meta:
        model = get_user_model()
        fields = ("first_name", "last_name", "email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.username = user.email
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.is_active = False
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user
