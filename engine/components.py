"""Component adapters (contract 1.2): how a feature's declared UI parts become parts of the page.

Adapter pattern. A feature declares a component in its own terms (`api.component(kind, **spec)`, or the
shortcuts `api.page(...)` and `api.slot(...)`). For each kind, one Adapter checks the spec against the
feature's folder and turns it into one standard description that the page understands:

    {"kind", "feature", "files", "module", ...kind's own fields}

The page has a matching adapter per kind (DuoComponents.adapters in web/index.html) that puts the
description on screen. Core code outside these two adapter sets never needs to know a kind.

To add a new kind of component: write an Adapter subclass here, add it to ADAPTERS and its name to
coreapi.COMPONENT_KINDS, and add the page-side adapter. A feature that asks for a kind with no adapter is
refused at register time with a message to write a core request.
"""
import re
from pathlib import Path

from . import coreapi

ID = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")


class Adapter:
    kind = ""

    def check(self, feature, spec, feature_dir):
        """Return the spec's own fields, checked. Raise ValueError with a clear reason when it is wrong."""
        raise NotImplementedError

    def describe(self, feature, fields):
        """The kind's fields for the page (added to kind, feature, files, module)."""
        return dict(fields)

    @staticmethod
    def js_file(feature_dir, file):
        rel = Path(str(file or ""))
        if not str(file or "").endswith(".js") or rel.is_absolute() or ".." in rel.parts:
            raise ValueError("A component file must be a .js file inside the feature folder: %r." % file)
        if not (Path(feature_dir) / rel).is_file():
            raise ValueError("The component file %s does not exist in the feature folder." % file)
        return str(rel)

    @staticmethod
    def order(spec):
        o = spec.get("order", 100)
        if not isinstance(o, int) or isinstance(o, bool) or not 0 <= o <= 1000:
            raise ValueError("order must be a whole number from 0 to 1000.")
        return o


class PageAdapter(Adapter):
    """A full page of its own: a side bar button (nav "side") or no button (nav "hidden", opened by link),
    and the feature's module fills the main view. Address: #/f/<feature>/<id>."""
    kind = "page"

    def check(self, feature, spec, feature_dir):
        pid, title = spec.get("id"), spec.get("title")
        if not isinstance(pid, str) or not ID.match(pid):
            raise ValueError("A page id must be lower case letters, digits, - and _: %r." % pid)
        if not isinstance(title, str) or not title.strip() or len(title) > 40:
            raise ValueError("A page needs a title of 1 to 40 characters.")
        nav = spec.get("nav", "side")
        if nav not in ("side", "hidden"):
            raise ValueError("A page's nav must be side or hidden.")
        return {"id": pid, "title": title.strip(), "file": self.js_file(feature_dir, spec.get("file")),
                "nav": nav, "order": self.order(spec)}

    def describe(self, feature, f):
        return {"id": f["id"], "key": "f:%s:%s" % (feature, f["id"]), "title": f["title"], "nav": f["nav"], "order": f["order"]}


class PanelAdapter(Adapter):
    """A panel inside a named place that core owns (contract 1.0 slots)."""
    kind = "panel"

    def check(self, feature, spec, feature_dir):
        slot = spec.get("slot")
        if slot not in coreapi.SLOTS:
            raise ValueError("Unknown slot %r. Known slots: %s." % (slot, ", ".join(coreapi.SLOTS)))
        return {"slot": slot, "file": self.js_file(feature_dir, spec.get("file")), "order": self.order(spec)}

    def describe(self, feature, f):
        return {"slot": f["slot"], "order": f["order"]}


ADAPTERS = {a.kind: a for a in (PageAdapter(), PanelAdapter())}


def adapt(feature, kind, spec, feature_dir):
    """One feature component -> the standard description the page uses."""
    a = ADAPTERS.get(kind)
    if a is None:
        raise ValueError("Core has no adapter for the component kind %r. Write a core request for it." % kind)
    fields = a.check(feature, dict(spec or {}), feature_dir)
    out = {"kind": kind, "feature": feature}
    out.update(a.describe(feature, fields))
    out["module"] = "/f/%s/%s" % (feature, fields["file"])
    out["files"] = [fields["file"]]
    return out


def start_page_ok(value):
    """Valid first screens: the core Home page, the New task page, or a feature page f:<feature>:<id>."""
    if value in ("home", "new", "features"):
        return True
    m = re.match(r"^f:([a-z][a-z0-9_]{0,40}):([a-z][a-z0-9_-]{0,40})$", str(value))
    return bool(m) and isinstance(value, str)
