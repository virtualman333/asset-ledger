from rest_framework import generics, permissions
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import RegisterSerializer, UserSerializer


class RegisterView(generics.CreateAPIView):
    """注册。开发期放开，生产可加邀请码。"""

    permission_classes = (permissions.AllowAny,)
    serializer_class = RegisterSerializer


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


register_view = RegisterView.as_view()
token_view = TokenObtainPairView.as_view()
token_refresh_view = TokenRefreshView.as_view()
me_view = MeView.as_view()
