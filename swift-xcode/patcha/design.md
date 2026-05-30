# Patcha macOS App — Design System

---

## Colors

### Brand

| Token | Hex | Usage |
|-------|-----|-------|
| `PatchaTheme.accent` | `#00CE93` | Primary interactive elements, active states, CTA buttons, toggles |

### Backgrounds

| Token | Light | Dark | Usage |
|-------|-------|------|-------|
| `PatchaTheme.bg(for:)` | `.white` | `.black` | Window/pane backgrounds |
| Card surface | `Color.primary.opacity(0.04)` | `Color.primary.opacity(0.04)` | Tiles, consent cards, source tiles |
| Info card (accent-tinted) | `PatchaTheme.accent.opacity(0.10)` | `PatchaTheme.accent.opacity(0.10)` | Privacy info card |

### Borders & Dividers

| Token | Value | Usage |
|-------|-------|-------|
| `PatchaTheme.softDivider` | `Color.primary.opacity(0.08)` | Horizontal dividers, card strokes |
| Field border | `Color.primary.opacity(0.10–0.12)` | Text field outlines (light/dark) |
| Card stroke | `Color.primary.opacity(0.15)` | Google button border, secondary borders |
| Accent card stroke | `PatchaTheme.accent.opacity(0.35)` | Privacy info card border |

### Text

| Role | Value |
|------|-------|
| Primary | `Color.primary` |
| Secondary | `Color.secondary` |
| Tertiary | `Color.tertiary` |
| On-accent | `PatchaTheme.bg(for: colorScheme)` — `.black` in dark mode, `.white` in light mode |

### Status

| State | Color |
|-------|-------|
| Success / Active | `Color.green` |
| Warning | `Color.orange` / `Color.yellow` |
| Error | `Color.red` |
| Disabled | Accent at `0.25–0.40` opacity |

---

## Typography

### Font Families

| Family | Variants | Usage |
|--------|----------|-------|
| **Britanica** | Bold, Semi Expanded Bold (+ full family registered) | Bold: all UI text; Semi Expanded Bold: section header titles (CAPS, `Color.primary`) |
| **InstrumentSerif** | Regular, Italic | Display/hero headings in onboarding only |
| **SF Pro** (system) | — | SF Symbol sizing only (not used for text) |

Helper extensions: `.britanica(_ size:)`, `.instrumentSerif(_ size:)`, `.instrumentSerifItalic(_ size:)`

### Size Scale

| Size | Weight | Usage |
|------|--------|-------|
| 11pt | Medium | Small labels, secondary actions |
| 12pt | Regular / Medium | Captions, body, field labels |
| 12.5pt | Medium | Accessibility banner, action text |
| 13pt | Regular / Semibold | Input fields, small buttons |
| 13.5pt | Medium | Pill button labels |
| 14pt | Regular / Semibold | Body text, onboarding primary buttons |
| 15pt | Medium | Prominent secondary text |
| 16pt | Semibold | Section headers, Patcha logo label |
| 18pt | Semibold | Permission icon size reference |
| 22pt | Semibold | Login view section headers |
| 36pt | Regular | Avatar placeholder text |
| 40pt | — | Placeholder pane icons (SF Symbol) |
| 44pt | Regular (InstrumentSerif) | Onboarding large headings |
| 56pt | Rounded | Login gradient background display text |

---

## Spacing & Layout

### Padding

| Value | Typical Use |
|-------|-------------|
| 2–4pt | Fine spacing between stacked labels |
| 6–8pt | Icon-to-text gaps, internal element spacing |
| 9pt | Button vertical padding |
| 10–12pt | Permission row padding, form field spacing |
| 14pt | SourceTile internal padding |
| 16pt | Sidebar nav items, permission rows horizontal |
| 18pt | Info cards, consent cards |
| 20pt | Settings pane side padding |
| 22pt | Grant button horizontal padding |
| 24pt | Primary onboarding button horizontal padding |
| 36pt | Login form horizontal padding |
| 48pt | Onboarding section vertical padding |
| 80pt | Onboarding content center padding (large screens) |

### Corner Radii

| Value | Use |
|-------|-----|
| 7pt | Checkbox |
| 8pt | Text fields, accent buttons, app icons |
| 9pt | Icon boxes inside tiles and cards |
| 10pt | Sidebar nav items, permission rows |
| 12pt | Consent cards, permission cards |
| 14pt | SourceTiles, app list containers, info cards |
| Capsule | Onboarding primary CTA, pill buttons, search field |

### Fixed Dimensions

| Element | Size |
|---------|------|
| Sidebar width | 220pt |
| Login form pane width | 400pt |
| Login form max content width | 320pt |
| App icons (lists) | 28 × 28pt |
| Icon boxes in tiles | 36 × 36pt |
| Icon boxes (permissions) | 48 × 48pt |
| Account pane avatar | 40 × 40pt |
| Consent card checkbox | 28 × 28pt |
| Status dot indicator | 8 × 8pt circle |

---

## Pages / Screens

### LoginView
Two-pane layout: fixed 400pt left form pane + right gradient/image background.
Contains email and password fields, sign-in/sign-up toggle, Google OAuth button, and inline error messaging. Background uses `login-bg.png` with a gradient overlay.

### OnboardingView
Three sequential steps shown inside a centered, padded container.

| Step | Content |
|------|---------|
| **ProfileOnboardingStep** | Collects user role, referral source, AI tools used, and feedback opt-in via pill button groups |
| **PermissionsOnboardingStep** | Requests Screen Recording, Accessibility, and Full Disk Access permissions with real-time grant-status indicators |
| **PrivacyOnboardingStep** | Shows an accent-tinted privacy info card and an embedded app-exclusion list |

### SettingsRootView
Root container that routes to LoginView, OnboardingView, or the main settings layout (sidebar + pane) depending on auth/onboarding state.

### Settings Sidebar (SettingsSidebar)
Fixed 220pt left rail. Navigation items are icon + label rows with 10pt corner radius selection highlight. Bottom shows app version and app icon. Sections in order: Permissions, General, Memories, Model Preference, Integrations, Account.

### AppPermissionsPane
Split into two sections:
- **Background Sources** — three SourceTile cards (Terminal, Git, Browser) with toggle and accent icon box
- **App Exclusions** — capsule search field, scrollable app list with 28×28 icons and per-app toggles, plus a Rescan button

### GeneralPane
Behavior toggles (pause when focused, launch at login, auto-update). Pause duration selector: 30 min, 1 h, 2 h, 4 h, until tomorrow. Save & Restart Daemon button with inline status feedback.

### AccountPane
User profile row: 40×40 circular avatar, display name, email. Sign Out button below.

### IntegrationsPane
MCP Server connection status (8×8 color dot). Claude Code and Claude Desktop action buttons with status labels. ChatGPT/OpenAI MCP URL copy section.

### PlaceholderPane
Coming-soon view used for Memories and Model Preference. Large centered SF Symbol (40pt) with secondary label.

### AccessibilityBanner
Sticky yellow warning banner at top. Shown when Accessibility permission is missing. Contains a warning icon, message text, and an "Open Settings" link button.

---

## Design Rules

### Accent foreground color rule
Any element (text, icon, or symbol) rendered on a solid `PatchaTheme.accent` background must use `PatchaTheme.bg(for: colorScheme)` as its foreground color:
- **Dark mode** → `.black` on green background
- **Light mode** → `.white` on green background

This applies to: `AccentButtonStyle`, `OnboardingPrimaryButtonStyle`, selected pill buttons, selected sidebar nav items, icon boxes with solid accent fill (`SourceTile`, info card), and any custom accent-filled element. Never hardcode `.white` or `.black` as a foreground on an accent background.

---

## Components

### Buttons

#### AccentButtonStyle (Primary) — shared, defined in `PatchaTheme.swift`
- Background: `PatchaTheme.accent` (disabled: 0.4 opacity, pressed: 0.8 opacity)
- Shape: RoundedRectangle 8pt continuous
- Padding: 9pt vertical; horizontal 16pt (inline) or 0pt when `fullWidth: true`
- Font: 13pt semibold
- Text: `PatchaTheme.bg(for: colorScheme)` — black in dark mode, white in light mode
- Parameters: `isDisabled: Bool = false`, `fullWidth: Bool = false`
- Used by: Login submit button (`fullWidth: true`), GeneralPane "Save & Restart Daemon"

#### OnboardingPrimaryButtonStyle (Onboarding CTA)
- Background: `PatchaTheme.accent` (disabled: 0.4, pressed: 0.8)
- Shape: Capsule
- Padding: 24pt horizontal, 11pt vertical
- Font: 14pt semibold
- Text: `PatchaTheme.bg(for: colorScheme)` — black in dark mode, white in light mode

#### GoogleButtonStyle (OAuth)
- Background: White (light) / `Color.white.opacity(0.06–0.10)` (dark)
- Border: 1pt `Color.primary.opacity(0.15)`
- Shape: RoundedRectangle 8pt continuous
- Opacity on press: 0.85
- Font: 12.5pt medium
- Leading 18×18 Google SVG logo

#### GrantButtonStyle (Permission Grant)
- Background: `Color.black.opacity(0.9)` (pressed: 0.7)
- Shape: RoundedRectangle 8pt continuous
- Padding: 22pt horizontal, 9pt vertical
- Font: 13pt semibold white

### Text Fields

#### LoginField
- Background: `Color.primary.opacity(0.04–0.08)`
- Border: 1pt `Color.primary.opacity(0.10–0.12)` stroke, RoundedRectangle 8pt
- Padding: 12pt horizontal, 9pt vertical
- Font: 13pt regular
- Label above: 11pt medium, secondary color

### Cards

#### SourceTile (AppPermissionsPane)
- Layout: top row → icon + name + toggle; bottom row → description (full width)
- Background: `Color.primary.opacity(0.05)`
- Border: 1pt `PatchaTheme.softDivider`, RoundedRectangle 14pt continuous
- Padding: 14pt all sides; 8pt between rows, 10pt between icon and name
- Icon box: 32×32, rounded 9pt; accent fill (0.9 when on, 0.25 when off)
- Icon color: `PatchaTheme.bg(for: colorScheme)` when on, `.primary.opacity(0.6)` when off

#### Consent Card (Onboarding)
- Background: `Color.primary.opacity(0.04)`
- Border: 1pt `PatchaTheme.softDivider` stroke, RoundedRectangle 12pt
- Padding: 18pt
- Custom checkbox: 28×28, accent fill, `checkmark` symbol 14pt bold — color: `PatchaTheme.bg(for: colorScheme)`

#### Info Card (PrivacyOnboardingStep)
- Background: `PatchaTheme.accent.opacity(0.10)`
- Border: 1pt `PatchaTheme.accent.opacity(0.35)`, RoundedRectangle 14pt
- Padding: 18pt
- Leading icon box: 36×36, rounded 9pt, filled accent — `checkmark.shield.fill` 16pt semibold, color: `PatchaTheme.bg(for: colorScheme)`

### Toggles
- Style: native `.toggleStyle(.switch)`
- Tint: `PatchaTheme.accent`
- Used in: SourceTiles, behavior settings, consent checkbox row

### Pill Buttons (Onboarding selection groups)
- Shape: Capsule continuous
- Background: accent (selected) / `Color.primary.opacity(0.08)` (hover) / `Color.primary.opacity(0.04)` (default)
- Border: 1pt `PatchaTheme.softDivider` (hidden when selected)
- Padding: 16pt horizontal, 9pt vertical
- Font: 13.5pt medium
- Text: `PatchaTheme.bg(for: colorScheme)` when selected (black dark / white light), `Color.primary` otherwise

### Dividers

#### SoftDivider
- 1pt height rectangle
- Fill: `PatchaTheme.softDivider` (`Color.primary.opacity(0.08)`)
- Used between sections and as card strokes

#### "or" Divider (Login)
- Two SoftDivider lines flanking a centered "or" label
- Label: `.caption2` font, secondary color

### Search Field (AppPermissionsPane)
- Shape: Capsule
- Background: `Color.primary.opacity(0.05)` fill
- Border: 1pt `PatchaTheme.softDivider`
- Leading `magnifyingglass` icon: 12pt, secondary color
- Padding: 12pt horizontal, 8pt vertical

### Status Dot
- Shape: 8×8 Circle
- Color: `Color.green` (connected) / `Color.red` (disconnected)
- Used in IntegrationsPane next to MCP server label

---

## Icons & Assets

### Images
| Asset | File | Usage |
|-------|------|-------|
| Login background | `login-bg.png` | Right pane of LoginView |
| Google logo | `google.svg` | 18×18 in GoogleButtonStyle |

### Icon Style Rule
All SF Symbols must use the **filled, rounded** variant (`symbolRenderingMode` default, `.fill` suffix where available). Never use outline/stroke-only symbols. This keeps the icon language consistent — solid shapes that read clearly at small sizes in both light and dark mode.

### SF Symbols
| Symbol | Usage |
|--------|-------|
| `shield.fill` | Permissions sidebar item |
| `gearshape.fill` | General sidebar item |
| `clock.fill` | Memories sidebar item |
| `cpu.fill` | Model Preference sidebar item |
| `link` | Integrations sidebar item (no `.fill` variant in SF Symbols) |
| `person.circle.fill` | Account sidebar item |
| `exclamationmark.triangle.fill` | Accessibility banner warning |
| `magnifyingglass` | Search field icon |
| `checkmark` | Consent checkbox checkmark (on solid accent bg, no fill needed) |
| `checkmark.circle.fill` | Permission granted status |
| `checkmark.shield.fill` | Privacy info card icon |
| `display.fill` | Screen recording permission icon |
| `dot.circle.fill` | Accessibility permission icon |
| `doc.text.fill` | Full disk access permission icon |
| `app.fill` | Fallback icon for apps without system icon |
| `arrow.triangle.branch` | Git source tile (no `.fill` variant in SF Symbols) |
