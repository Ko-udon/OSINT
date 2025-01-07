from django.apps import AppConfig
import os

class GithubConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'github'
    
    def ready(self):
        if os.environ.get('RUN_MAIN', None) is not None: 
            from .views import scheduler
            scheduler.start()