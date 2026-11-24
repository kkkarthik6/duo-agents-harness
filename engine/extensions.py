"""Declared feature controls. UI changes use the same validation as HTTP writes."""
import math
import re

from . import config, coreapi


def setting_spec(key, default, label=None, description="", choices=None, minimum=None, maximum=None):
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", key):
        return None  # Preserve legacy setting keys without generating a control.
    # Secrets and complex values stay out of generated controls and API responses.
    if re.search(r"secret|password|token|credential|private_key|api_key", key, re.I):
        return None
    kind = {bool: "bool", int: "int", float: "number", str: "text"}.get(type(default))
    if kind is None:
        return None
    spec = {"key": key, "label": str(label or key.replace("_", " ").capitalize()),
            "description": str(description), "type": kind, "default": default}
    if choices is not None:
        if not isinstance(choices, (tuple, list)) or not choices or any(type(x) is not type(default) for x in choices):
            raise ValueError("Setting choices must match the default type")
        spec["choices"] = list(choices)
    for name, value in (("minimum", minimum), ("maximum", maximum)):
        if value is not None:
            if kind not in ("int", "number") or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("Setting bounds require finite numbers")
            spec[name] = value
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Setting minimum exceeds maximum")
    validate_value(spec, default)
    return spec


def validate_value(spec, value):
    kind = spec["type"]
    ok = {"bool": type(value) is bool, "int": type(value) is int,
          "number": type(value) in (int, float), "text": type(value) is str}[kind]
    if not ok or (kind in ("int", "number") and not math.isfinite(value)):
        raise ValueError("%s needs a %s value" % (spec["key"], kind))
    if kind == "text" and len(value) > 4000:
        raise ValueError("Setting text exceeds 4000 characters")
    if "choices" in spec and value not in spec["choices"]:
        raise ValueError("%s is not one of the declared choices" % spec["key"])
    for key, invalid in (("minimum", lambda x: value < x), ("maximum", lambda x: value > x)):
        if key in spec and invalid(spec[key]):
            raise ValueError("%s is outside its declared range" % spec["key"])
    return value


def component_id(component):
    return component.get("key") or "panel:%s:%s" % (component["slot"], component["module"])


def apply_layout(registry):
    controls = config.CFG.get("feature_controls", {})
    for component in registry.components:
        cid = component_id(component)
        component["control_id"] = cid
        patch = controls.get(component["feature"], {}).get("layout", {}).get(cid, {})
        component.update(patch)
    registry.slots = {slot: [] for slot in coreapi.SLOTS}
    for c in registry.components:
        if c["kind"] == "panel" and c.get("visible", True):
            registry.slots[c["slot"]].append((c["feature"], c["files"][0]))


def view(registry, disabled):
    data = registry.view()
    data["extension_points"] = {"slots": list(coreapi.SLOTS), "events": list(coreapi.EVENTS),
                                "stages": list(coreapi.CORE_STAGES), "components": list(coreapi.COMPONENT_KINDS)}
    data["disabled"] = disabled
    for feature in data["features"]:
        name = feature["name"]
        feature["enabled"] = config.CFG.get("feature_controls", {}).get(name, {}).get("enabled", True)
        feature["blocked"] = name in disabled
        if feature["blocked"]:
            feature["problems"] = [disabled[name].get("reason", "The feature failed a core check")]
        feature["controls"] = []
        for key, spec in registry.setting_specs.get(name, {}).items():
            value = config.CFG.get("features", {}).get(name, {}).get(key, spec["default"])
            feature["controls"].append(dict(spec, value=value))
        feature["components"] = [{k: v for k, v in c.items() if k != "files"}
                                  for c in registry.components if c["feature"] == name]
        feature["hooks"] = [event for event, handlers in registry.hooks.items() if any(f == name for f, _ in handlers)]
        feature["stages"] = [{"name": stage.name, "after": stage.after} for f, stage in registry.stages if f == name]
        feature["gates"] = [{"name": gate.name, "after": gate.after} for f, gate in registry.gates if f == name]
    return data


def configure(registry, payload, disabled=()):
    if not isinstance(payload, dict) or set(payload) - {"feature", "enabled", "values", "layout"}:
        raise ValueError("Use feature, enabled, values, and layout fields")
    name = payload.get("feature")
    if name not in registry.manifests:
        raise ValueError("Unknown feature")
    import copy
    controls = copy.deepcopy(config.CFG.get("feature_controls", {}))
    control = controls.setdefault(name, {})
    values = copy.deepcopy(config.CFG.get("features", {}))
    if "enabled" in payload:
        if type(payload["enabled"]) is not bool:
            raise ValueError("enabled must be true or false")
        if payload["enabled"] and name in disabled:
            raise ValueError("This feature failed a core check. Repair it before enabling it")
        control["enabled"] = payload["enabled"]
    if "values" in payload:
        if not isinstance(payload["values"], dict):
            raise ValueError("values must be an object")
        specs = registry.setting_specs.get(name, {})
        for key, value in payload["values"].items():
            if key not in specs:
                raise ValueError("Setting is not exposed by this feature: " + key)
            values.setdefault(name, {})[key] = validate_value(specs[key], value)
    if "layout" in payload:
        if not isinstance(payload["layout"], dict):
            raise ValueError("layout must be an object")
        components = {c["control_id"]: c for c in registry.components if c["feature"] == name}
        for cid, patch in payload["layout"].items():
            if cid not in components or not isinstance(patch, dict) or set(patch) - {"visible", "slot", "order"}:
                raise ValueError("Unknown component or layout field")
            if "visible" in patch and type(patch["visible"]) is not bool:
                raise ValueError("visible must be true or false")
            if "slot" in patch and (components[cid]["kind"] != "panel" or patch["slot"] not in coreapi.SLOTS):
                raise ValueError("Panels must use a declared slot")
            if "order" in patch and (type(patch["order"]) is not int or not 0 <= patch["order"] <= 1000):
                raise ValueError("Component order must be between 0 and 1000")
            control.setdefault("layout", {}).setdefault(cid, {}).update(patch)
    return {"feature_controls": controls, "features": values}
