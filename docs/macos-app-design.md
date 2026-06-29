# macOS App Design

## Overview

Patcha is a **menu bar app** with a modal settings window. The main entry point is a status bar icon; the full UI lives in a 900x620 window managed by `SettingsWindowController`.

---

## App States

The app presents different UI depending on auth/onboarding state, managed by `SettingsRootView`:

| State | UI Shown |
|---|---|
| Not signed in | `LoginView` |
| Signed in, onboarding incomplete | `OnboardingView` |
| Signed in, onboarded | Settings window (sidebar + pane) |

---

## Menu Bar

**File:** `MenuBarController.swift`

The persistent entry point. Icon reflects daemon state:

- Recording → `icon-recording.svg`
- Paused → `icon-paused.svg`
- Error → `icon-error.svg`
- Starting/Stopped → `icon-starting.svg`

**Menu items:**

- Grant Accessibility Access *(hidden if already granted)*
- Grant Screen Recording Access *(hidden if already granted)*
- MCP Server Status — shows running/stopped, click to copy URL
- Pause Recording submenu: 30 min / 1 hr / 2 hr / 4 hr / Until tomorrow / Resume Now
- Restart Daemon
- Preferences... (`Cmd+,`)
- Resources → Visit patcha.app
- Quit Patcha (`Cmd+Q`)

---

## Login View

**File:** `LoginView.swift`

Two-column layout.

**Left pane (400px):**
- Heading: "Welcome back" or "Create your account"
- Email field
- Password field
- Error message (if any)
- Primary button: Sign In / Create Account (disabled until fields filled)
- Divider ("or")
- Google OAuth button
- Toggle: switch between sign in and sign up modes

**Right pane:**
- Background image or gradient with "Patcha" hero text
- Adapts to light/dark mode

---

## Onboarding Flow

**File:** `OnboardingView.swift`

Three sequential steps. Window resizes between steps.

### Step 1 — Profile

- Role selection: "What do you do?" (pill buttons, required)
- Source selection: "Where'd you hear about Patcha?" (pill buttons, required)
- AI tools: "Which AI tools do you use today?" (multi-select pills)
- Consent checkbox: opt in to feedback outreach
- Continue button (disabled until role + source selected)

### Step 2 — Permissions

- Screen Recording — icon, description, Grant button / Granted badge
- Accessibility — icon, description, Grant button / Granted badge
- Full Disk Access — icon, description, Grant button / Granted badge
- Re-check button (appears after first grant attempt)
- Footer: "All three are required."
- Continue button (disabled until all three granted)

### Step 3 — Privacy

- Heading: "Decide what stays private."
- Info card: "Passwords, banking, and sensitive apps are off-limits by default"
- Embedded `AppPermissionsPane` so the user can configure exclusions during onboarding
- Continue button

---

## Main Settings Window

**File:** `SettingsRootView.swift`, `SettingsWindowController.swift`

Size: 900x620, hidden titlebar, resizable.

Layout:
- Top: `AccessibilityBanner` (only shown if Accessibility permission not granted)
- Left: `SettingsSidebar` (220px fixed width)
- Right: Active pane (fills remaining space)

---

## Sidebar

**File:** `SettingsSidebar.swift`

Navigation items (top to bottom):

| Section | Icon |
|---|---|
| Permissions | `shield.fill` |
| General | `gearshape.fill` |
| Memories | `clock.fill` *(placeholder)* |
| Model Preference | `cpu.fill` *(placeholder)* |
| Integrations | `link` |
| Account | `person.circle.fill` |

- Active item: accent-colored background, white label text
- Bottom: app icon + version string

---

## Panes

### Permissions Pane

**File:** `Panes/AppPermissionsPane.swift`

**Section: Background Sources**

Three source tiles, each with an icon badge, name, description, and toggle:

| Source | Description |
|---|---|
| Terminal | Shell history, commands, output |
| Git | Commits, branches, staging |
| Browser history | URLs and titles you visit |

Tile appearance: colored icon background when enabled, semi-transparent when disabled.

**Section: Exclude Specific Apps**

- Search bar (capsule style)
- Rescan button (text link)
- Scrollable list of installed apps — each row shows app icon, name, toggle
- States: scanning (progress indicator), empty, populated

---

### General Pane

**File:** `Panes/GeneralPane.swift`

**Section: Behavior** (toggle rows)

- Pause collection when Patcha is focused
- Launch at Login
- Check for updates automatically

**Section: Pause Patcha**

*When not paused:*
- Picker: 30 min / 1 hr / 2 hr / 4 hr / Until tomorrow
- "Pause Recording" button

*When paused:*
- Status: "Paused · resumes [relative time]"
- "Resume Now" button

**Footer:**
- "Save & Restart Daemon" button
- Transient "Saved. Restarting daemon..." message (fades after 3s)

---

### Integrations Pane

**File:** `Panes/IntegrationsPane.swift`

**Section: MCP Server**

- Status indicator: green/red circle + URL (`http://127.0.0.1:[port]/mcp/`) or "Not running"
- URL is clickable to copy

**Section: Connect AI Clients**

| Client | Controls |
|---|---|
| Claude Code | Status (idle/connected/failed) + Connect button |
| Claude Desktop | Status (idle/connected/failed) + Connect button |
| ChatGPT / OpenAI | Copy MCP URL button with "Copied!" feedback |

Connect writes to client config files:
- Claude Code: `~/.claude.json` (`mcpServers.patcha` entry)
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`

**Info text (footer):**
- Instructions for ChatGPT and OpenAI API integration

---

### Account Pane

**File:** `Panes/AccountPane.swift`

**Section: Account**

- Avatar (40x40 circle, from URL or fallback icon)
- Display name (if available)
- Email address (selectable text)

**Section: Session**

- "Signed in" label + "Sign Out" button
- Button state: "Signing out…" + disabled during logout
- Error message if logout fails

---

### Placeholder Pane

**File:** `Panes/PlaceholderPane.swift`

Used for sections not yet built:

- Centered section icon (large)
- "Coming soon" text

Currently used for: **Memories**, **Model Preference**

---

## Accessibility Banner

**File:** `AccessibilityBanner.swift`

Shown at the top of the settings window when Accessibility permission is not granted.

- Warning icon (orange triangle)
- Message: "Accessibility is off — Patcha can't read the focused window."
- "Open Settings" button
- Yellow background
- Auto-refreshes every 2 seconds and on app activation

---

## Theme

**File:** `PatchaTheme.swift`

| Token | Value |
|---|---|
| Accent | `#00CE93` (green) |
| Soft divider | `Color.primary.opacity(0.08)` |
| Background (light) | White |
| Background (dark) | `#1A1A1A` |

**Custom fonts:** Britanica (Regular, Bold, Semi-Expanded, Extra-Bold), InstrumentSerif (Regular, Italic)

**Button styles:** `AccentButtonStyle` (green, 8px radius, opacity on press, optional full-width)
