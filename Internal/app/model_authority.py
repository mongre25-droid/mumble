"""Shared fail-closed model discovery and provider activation authority."""

from hashlib import sha256
import json
from uuid import uuid4


AUTHORITY_KEY = "_confirmed_text_models"
ACTIVATION_KEY = "__provider_activation__"
SYNCHRONOUS_RELOAD_KEYS = frozenset((AUTHORITY_KEY, "llm_provider"))


def controller_reload_response(apply_change, changed_key, start_background):
    """Apply privacy-sensitive reloads before acknowledging their caller."""
    changed_key = str(changed_key or "")
    if changed_key in SYNCHRONOUS_RELOAD_KEYS:
        if not apply_change(changed_key):
            return {"ok": False, "message": "Settings reload failed."}
        return {"ok": True}
    start_background(lambda: apply_change(changed_key))
    return {"ok": True}


def mapping_matches_defaults(mapping, defaults):
    """Reject JSON-valid settings whose known values have impossible shapes."""
    if not isinstance(mapping, dict):
        return False
    for key, value in mapping.items():
        if key not in defaults or defaults[key] is None:
            continue
        expected = defaults[key]
        if isinstance(expected, bool):
            valid = isinstance(value, bool)
        elif isinstance(expected, int):
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif isinstance(expected, float):
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            valid = isinstance(value, type(expected))
        if not valid:
            return False
    return True


def storage_snapshot(primary, backup, memory, validate_mapping):
    """Apply one shared valid/missing/invalid authority storage contract."""
    primary_state, primary_data = primary
    backup_state, backup_data = backup
    for state, data in (primary, backup):
        if state == "invalid" or (state == "ok" and not validate_mapping(data)):
            raise ValueError("settings primary or backup is invalid")
    if primary_state == "ok":
        source = primary_data
    elif backup_state == "ok":
        source = backup_data
    else:
        source = memory
    if not validate_mapping(source):
        raise ValueError("settings authority snapshot is invalid")
    return json.loads(json.dumps(source))


def storage_update(primary, backup, memory, dirty, validate_mapping, mutator):
    """Prepare one full transaction while preserving unrelated pending values."""
    current = storage_snapshot(primary, backup, memory, validate_mapping)
    for key in dirty:
        if key in memory:
            current[key] = json.loads(json.dumps(memory[key]))
        else:
            current.pop(key, None)
    result = mutator(current)
    if not validate_mapping(current):
        raise ValueError("model authority update produced invalid settings")
    return json.loads(json.dumps(current)), result


def model_credential_identity(provider, api_key):
    """Return a non-secret identity binding confirmation to one credential."""
    material = f"{str(provider).strip().lower()}\0{str(api_key).strip()}"
    return sha256(material.encode("utf-8")).hexdigest()


def _record_is_current(entry, identity, generation, request_id, state):
    return (
        isinstance(entry, dict)
        and entry.get("credential_identity") == identity
        and entry.get("generation") == generation
        and entry.get("request_id") == request_id
        and entry.get("state") == state
    )


def confirmed_models_for(settings, provider, api_key):
    """Return exact acknowledged models from the latest durable snapshot."""
    try:
        state = settings.authority_read()
    except Exception:
        return ()
    records = state.get(AUTHORITY_KEY, {}) if isinstance(state, dict) else {}
    entry = records.get(provider, {}) if isinstance(records, dict) else {}
    identity = model_credential_identity(provider, api_key)
    if not isinstance(entry, dict):
        return ()
    if not _record_is_current(
        entry, identity, entry.get("generation"), entry.get("request_id"),
        "confirmed",
    ):
        return ()
    if entry.get("confirmed_generation") != entry.get("generation"):
        return ()
    models = entry.get("models", ())
    if not isinstance(models, (list, tuple)):
        return ()
    return tuple(str(model).strip() for model in models if str(model).strip())


class ModelDiscoveryAuthority:
    """One durable, cross-process discovery and activation state machine."""

    def __init__(
        self, settings, providers, fetch_models, controller_reload,
        supported_providers=None,
    ):
        self.settings = settings
        self.providers = providers
        self.fetch_models = fetch_models
        self.controller_reload = controller_reload
        self.supported_providers = frozenset(
            supported_providers or ("cerebras", "openrouter")
        )

    def _provider(self, provider):
        return str(provider or "").strip().lower()

    def _credential(self, provider):
        config = self.providers.get(provider) or {}
        key_setting = config.get("key_setting")
        return self.settings.get(key_setting, "") if key_setting else ""

    def _acknowledged(self, key):
        result = self.controller_reload(key)
        return isinstance(result, dict) and result.get("ok") is True

    def _update_records(self, mutator):
        def update(state):
            raw = state.get(AUTHORITY_KEY, {})
            records = dict(raw) if isinstance(raw, dict) else {}
            result = mutator(records)
            state[AUTHORITY_KEY] = records
            return result
        return self.settings.authority_update(update)

    def begin(self, provider, provider_activation=False):
        """Invalidate first and allocate a non-reusable durable request token."""
        provider = self._provider(provider)
        if provider not in self.supported_providers:
            return {"ok": False, "message": "Unsupported processing provider."}
        identity = model_credential_identity(provider, self._credential(provider))
        allocated = {}

        def allocate(records):
            previous = records.get(provider, {})
            try:
                generation = max(0, int(previous.get("generation", 0))) + 1
            except (AttributeError, TypeError, ValueError):
                generation = 1
            request_id = uuid4().hex
            records[provider] = {
                "credential_identity": identity,
                "generation": generation,
                "request_id": request_id,
                "state": "pending",
                "models": [],
            }
            allocated.update(generation=generation, request_id=request_id)
            if provider_activation:
                previous_activation = records.get(ACTIVATION_KEY, {})
                try:
                    activation_generation = max(
                        0, int(previous_activation.get("generation", 0))
                    ) + 1
                except (AttributeError, TypeError, ValueError):
                    activation_generation = 1
                activation_request_id = uuid4().hex
                records[ACTIVATION_KEY] = {
                    "provider": provider,
                    "generation": activation_generation,
                    "request_id": activation_request_id,
                }
                allocated.update(
                    activation_generation=activation_generation,
                    activation_request_id=activation_request_id,
                )

        try:
            self._update_records(allocate)
            if not self._acknowledged(AUTHORITY_KEY):
                return {
                    "ok": False, **allocated,
                    "message": "The live controller did not confirm model invalidation.",
                }
            return {"ok": True, **allocated}
        except Exception as error:
            return {"ok": False, "message": str(error)}

    def _invalidate(self, provider, api_key, generation, request_id):
        identity = model_credential_identity(provider, api_key)

        def invalidate(records):
            entry = records.get(provider, {})
            if _record_is_current(
                entry, identity, generation, request_id, entry.get("state")
            ):
                records[provider] = {
                    "credential_identity": identity,
                    "generation": generation,
                    "request_id": request_id,
                    "state": "unavailable",
                    "models": [],
                }

        self._update_records(invalidate)

    def _confirmation_is_current(
        self, provider, api_key, generation, request_id,
    ):
        state = self.settings.authority_read()
        records = state.get(AUTHORITY_KEY, {}) if isinstance(state, dict) else {}
        entry = records.get(provider, {}) if isinstance(records, dict) else {}
        return (
            _record_is_current(
                entry, model_credential_identity(provider, api_key),
                generation, request_id, "confirmed",
            )
            and entry.get("confirmed_generation") == generation
        )

    def list_models(self, provider, generation=None, request_id=None):
        provider = self._provider(provider)
        if provider not in self.supported_providers:
            return {"ok": False, "models": [],
                    "message": "Unsupported processing provider."}
        api_key = self._credential(provider)
        try:
            if generation is None:
                started = self.begin(provider)
                if not started.get("ok"):
                    return {"ok": False, "models": [],
                            "message": started.get("message", "Model check unavailable.")}
                generation = started["generation"]
                request_id = started["request_id"]
            else:
                generation = int(generation)
                request_id = str(request_id or "")
            models = [
                str(model).strip() for model in self.fetch_models(provider, api_key)
                if str(model).strip()
            ]
            if not models:
                self._invalidate(provider, api_key, generation, request_id)
                self._acknowledged(AUTHORITY_KEY)
                return {"ok": False, "models": [], "message": "No models returned."}
            identity = model_credential_identity(provider, api_key)
            staged = []

            def stage(records):
                entry = records.get(provider, {})
                current = _record_is_current(
                    entry, identity, generation, request_id, "pending"
                )
                staged.append(current)
                if current:
                    records[provider] = {
                        "credential_identity": identity,
                        "generation": generation,
                        "request_id": request_id,
                        "state": "discovered",
                        "models": models,
                    }

            self._update_records(stage)
            if not staged or not staged[0]:
                return self._obsolete()
            if not self._acknowledged(AUTHORITY_KEY):
                self._invalidate(provider, api_key, generation, request_id)
                return {"ok": False, "models": [],
                        "message": "The live controller did not confirm the model check."}
            completed = []

            def complete(records):
                entry = records.get(provider, {})
                current = _record_is_current(
                    entry, identity, generation, request_id, "discovered"
                )
                completed.append(current)
                if current:
                    entry["confirmed_generation"] = generation
                    entry["state"] = "confirmed"

            self._update_records(complete)
            if not completed or not completed[0]:
                return self._obsolete()
            if not self._acknowledged(AUTHORITY_KEY):
                self._invalidate(provider, api_key, generation, request_id)
                self._acknowledged(AUTHORITY_KEY)
                return {"ok": False, "models": [],
                        "message": "The live controller did not confirm the model check."}
            if not self._confirmation_is_current(
                provider, api_key, generation, request_id
            ):
                return self._obsolete()
            return {"ok": True, "models": models, "message": f"{len(models)} models"}
        except Exception as error:
            if generation is not None:
                try:
                    self._invalidate(provider, api_key, int(generation), str(request_id or ""))
                    self._acknowledged(AUTHORITY_KEY)
                except Exception:
                    pass
            return {"ok": False, "models": [], "message": str(error)}

    @staticmethod
    def _obsolete():
        return {"ok": False, "obsolete": True, "models": [],
                "message": "A newer model check replaced this one."}

    def activate_provider(self, provider):
        provider = self._provider(provider)
        started = self.begin(provider, provider_activation=True)
        if not started.get("ok"):
            return started
        result = self.list_models(
            provider, started["generation"], started["request_id"]
        )
        if not result.get("ok"):
            return result
        activated = []
        previous_provider = []

        def activate(state):
            records = state.get(AUTHORITY_KEY, {})
            entry = records.get(provider, {}) if isinstance(records, dict) else {}
            activation = records.get(ACTIVATION_KEY, {}) if isinstance(records, dict) else {}
            current = (
                entry.get("generation") == started["generation"]
                and entry.get("request_id") == started["request_id"]
                and entry.get("state") == "confirmed"
                and activation.get("provider") == provider
                and activation.get("generation") == started["activation_generation"]
                and activation.get("request_id") == started["activation_request_id"]
            )
            activated.append(current)
            if current:
                previous_provider.append(state.get("llm_provider"))
                state["llm_provider"] = provider

        try:
            self.settings.authority_update(activate)
        except Exception as error:
            return {"ok": False, "models": [], "message": str(error)}
        if not activated or not activated[0]:
            return {"ok": False, "obsolete": True, "models": [],
                    "message": "A newer provider choice replaced this one."}
        if not self._acknowledged("llm_provider"):
            def withdraw(state):
                records = state.get(AUTHORITY_KEY, {})
                activation = (
                    records.get(ACTIVATION_KEY, {})
                    if isinstance(records, dict) else {}
                )
                still_current = (
                    state.get("llm_provider") == provider
                    and activation.get("provider") == provider
                    and activation.get("generation") == started["activation_generation"]
                    and activation.get("request_id") == started["activation_request_id"]
                )
                if still_current:
                    state["llm_provider"] = previous_provider[0]
                    entry = records.get(provider, {})
                    if isinstance(entry, dict):
                        entry["state"] = "unavailable"
                        entry["models"] = []

            try:
                self.settings.authority_update(withdraw)
                self._acknowledged(AUTHORITY_KEY)
                self._acknowledged("llm_provider")
            except Exception:
                pass
            return {"ok": False, "models": [],
                    "message": "The live controller did not confirm provider activation."}
        return result
