"""The plugin's own webhook is gone, replaced by Newsflasharr routing."""


def test_the_webhook_settings_are_gone(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    ids = [f.get("id") for f in inst.fields]
    assert "webhook_url" not in ids
    assert "fire_webhook_on_completion" not in ids


def test_the_webhook_methods_are_gone(plugin_module):
    assert not hasattr(plugin_module.Plugin, "_fire_webhook")
    assert not hasattr(plugin_module.Plugin, "_build_webhook_body")


def test_nothing_still_calls_the_webhook(plugin_module):
    import inspect
    src = inspect.getsource(plugin_module)
    assert "_fire_webhook(" not in src
