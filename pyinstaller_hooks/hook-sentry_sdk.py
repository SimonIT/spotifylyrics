import sentry_sdk.integrations as si


def make_integration_name(integration_name: str):
    return ".".join(integration_name.split(".")[:-1])


hiddenimports = ["sentry_sdk.integrations.stdlib",
                 "sentry_sdk.integrations.excepthook",
                 "sentry_sdk.integrations.dedupe",
                 "sentry_sdk.integrations.atexit",
                 "sentry_sdk.integrations.modules",
                 "sentry_sdk.integrations.argv",
                 "sentry_sdk.integrations.logging",
                 "sentry_sdk.integrations.threading"].extend(map(make_integration_name, si._AUTO_ENABLING_INTEGRATIONS))
