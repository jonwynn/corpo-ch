from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, reverse_lazy

from corpoch import views
from corpoch.api import urls as apiurls

urlpatterns = [
    path('match-viewer/', include('corpoch.match_viewer_urls')),
    path('admin/login/', views.auth_start, name='login'),
    path('admin/', admin.site.urls),
    path('', views.null),
    path('auth',views.auth),
    path('auth/start', views.auth_start, name='discord_auth_start'),
    path('auth/user', views.user, name="user"),
    path('home', views.home, name="home"),
    path('api/', include('corpoch.api.urls')),
    path('livematches/', views.livematches, name="livematches"),
    path('privterms/', views.privterms, name="privterms"),
    path('update-live-matches/', views.update_livematches, name='update_livematches'),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
