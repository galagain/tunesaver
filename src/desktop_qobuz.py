"""Bounded Qobuz sign-in subprocess; credentials arrive only on private stdin."""
import json
import sys
import download_engine


def main():
    import requests
    import qobuz_dl.bundle as bundle_module
    from qobuz_dl.qopy import Client
    class BoundedSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", (5, 20))
            return super().request(*args, **kwargs)
    bundle_module.Session = BoundedSession
    payload = json.load(sys.stdin)
    bundle = bundle_module.Bundle()
    app_id = str(bundle.get_app_id())
    app_secrets = list(bundle.get_secrets().values())
    Client.from_token(payload["user_id"], payload["token"], app_id, app_secrets)
    download_engine.write_qobuz_config({**download_engine.load_qobuz_settings(),
        "user_id": payload["user_id"], "user_auth_token": payload["token"], "app_id": app_id,
        "secrets": ",".join(app_secrets),
        "tunesaver_login_id": download_engine.qobuz_login_id(payload["user_id"], payload["token"])})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Third-party exception URLs can contain the user's session token.
        print("Connexion Qobuz impossible.", file=sys.stderr)
        sys.exit(1)
