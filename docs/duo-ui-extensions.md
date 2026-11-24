# Extend Duo through the UI

Core 1.8.1 includes the Features screen introduced in 1.8.0. Open Features in the left sidebar.
The feature API contract is 1.3.0. Existing 1.x features remain supported.

## User controls

- Task engine: change the runtime multiplier, unknown-runtime fallback, turn limits, progress thresholds, and recovery limit.
- Installed features: enable or disable a feature, edit declared settings, and inspect load errors.
- UI elements: show or hide a component, move panels between declared locations, and change display order.
- Workflow connections: inspect event listeners and added stages or gates.
- Add a feature: open the existing feedback workflow with a feature request. Duo builds a version copy for review.

Feature settings reach the next feature call. Existing job deadlines remain fixed.
Availability and placement changes wait until active runs stop.
A feature disabled by a failed core check needs repair before it can be enabled.
Hidden components stop mounting. Hiding a component is a display preference, not an access control.

## Declare feature controls

Features keep their Python and JavaScript files in the existing user layer.
Import engine.coreapi only when importing engine code.

```python
from engine import coreapi

def register(api):
    api.setting("page_size", 20,
                label="Items per page",
                description="Number of saved tasks shown on each page.",
                minimum=1, maximum=100)
    api.setting("sort", "newest", choices=["newest", "oldest"])
    api.setting("show_notes", True, label="Show task notes")
    api.page("tasks", "Saved tasks", "ui/tasks.js")
    api.slot("home", "ui/summary.js")
    api.route("GET", "/preferences", lambda req: (
        200, {"page_size": req["ctx"].setting("page_size")}))
```

Boolean, integer, number, and text defaults receive generated controls.
Choices and numeric bounds apply to UI and HTTP writes.
Complex settings and secret-named settings are omitted from generated controls.
Legacy settings remain readable through Context.setting.
Sensitive credentials belong in the provider's credential system.

Features can already add event handlers with api.on, stages with api.stage, and decision gates with api.gate.
The Features screen exposes those registrations and available extension points.
These additions use the existing core interfaces. They do not replace command guards or agent sandbox rules.

## API and storage

- GET /api/extensions: feature controls, workflow registrations, components, errors, and core contract information.
- POST /api/extensions/configure: feature name with enabled, values, or layout changes.
- GET and POST /api/config: validated engine settings.
- The shared Duo configuration stores feature values under features and display preferences under feature_controls.

Only declared settings and known component IDs can be changed through the feature endpoint.
The endpoint rejects module replacement, unknown locations, and invalid values.
Global engine settings use a separate allowlist. Feature settings cannot change security rules.

## Desktop installation

The previous installed desktop app started an old engine from ~/Duo/source.
Opening that app could replace the current sealed core with the old runtime.
The installer now updates unsealed local desktop launchers to a small core-selection bridge.
That bridge selects ~/Duo/core/state.json without importing the bundled engine.
Original launchers are preserved under ~/Duo/backups/desktop-launchers/.
The helper does not change bundles with sealed signing resources. Those require a full signed app update.

Run sh install.sh from this source folder. Reopen Duo after the installer finishes.
Future app opens keep using the selected installed core.

## Validation

Checks cover typed settings, live route values, persistence, invalid changes, feature disabling, active-run protection, and component placement.
Desktop bridge checks cover current-core selection, path validation, backup preservation, repeat installation, and signed-resource protection.
Native desktop checks are recorded in contents.md after installation.

## Notification bar and text layout (core 1.8.2)

Notifications appear in a bar below the header. The bar uses document space and does not cover controls.
Click Notifications to expand its scrollable list. Click a message or its dismiss button to deactivate it.
Dismissed messages stay inactive during polling and after reopening the app. A changed outcome creates a new notification.
Dismissal is stored locally as a bounded list of message fingerprints. It does not delete the underlying error or feature version.
Retry, Stop, and Restart remain explicit actions inside the expanded list. Reading or dismissing a message does not run those actions.
Short confirmations expire automatically after five seconds.

The task view has separate grid rows for status, progress, progress text, timeline, and composer.
Duplicate progress elements were removed. Paused-run reasons use expandable details and appear once.
Feature labels wrap with consistent spacing. Long text, table cells, and anonymous agent names wrap within their own areas.
