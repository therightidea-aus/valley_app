from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        login = username or kwargs.get("email")
        if not login:
            return None

        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(email__iexact=login)
        except UserModel.DoesNotExist:
            try:
                user = UserModel.objects.get(username__iexact=login)
            except UserModel.DoesNotExist:
                UserModel().set_password(password)
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
