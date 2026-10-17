from pydantic import BaseModel

from app.settings.profile_core import ModelDefaults, ProviderDefaults, RoutingDefaults


class GlobalSettingsProfile(BaseModel):
    providers: ProviderDefaults = ProviderDefaults()
    models: ModelDefaults = ModelDefaults()
    routing: RoutingDefaults = RoutingDefaults()
