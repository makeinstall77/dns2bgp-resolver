from dns2bgp_resolver.container import AppContainer


def allowed(container: AppContainer, user_id: int | None) -> bool:
    allow = container.settings.telegram.allowed_user_ids
    if not allow:
        return False
    return user_id is not None and user_id in allow
