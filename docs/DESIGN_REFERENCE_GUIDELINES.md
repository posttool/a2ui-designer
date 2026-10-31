# Preparing design references (Figma, Stitch and screenshots)

This guide explains how to prepare Figma files and screenshots so the Design
Constitution Builder (A2UI Designer) can turn them into useful constitution rules, theme
tokens, and catalog changes. Following it gives you sharper rules and more
`exact` token values, with less cleanup afterwards.

## 1. Purpose

When you bring a Figma file, Stitch project, or set of screenshots into
**Imports** (`Imports → New import`), A2UI Designer analyzes the material into
region-cropped **extractions** (`component`, `template`, `token_reference`, or
`ignore`), asks clarifying questions where intent is ambiguous, and prepares a
selectable **merge plan** before changing your project. Your designs only teach
the model what you actually show it, so a small, deliberate set of screens works
better than a large pile of them.

### What the analyzer looks for

| It reads…                            | …and turns it into                    |
| ------------------------------------ | ------------------------------------- |
| Hierarchy, reading order, grouping,  | **Constitution rules** (layout        |
: what's above the fold                : only) + **Reusable templates**        :
| How many items a screen shows, when  | Density rules ("≤3 items, then a      |
: you switch to list or tabs           : List")                                :
| Content / Loading / Empty / Error    | State rules                           |
: versions of a screen                 :                                       :
| Screens or sections composed from    | **Template extractions** (`template`) |
: repeating building blocks            : saved to Design system → Templates |  :
| Reusable multi-part UI cards, rows,  | **Component extractions**             |
: bars, headers, and widgets           : (`component`) drafted as custom A2UI  :
:                                      : | components                          :
| How each viewport changes the layout | Per-viewport rules                    |
| Fills, radii, text styles,           | **Token reference extractions**       |
: auto-layout spacing, variables       : (`token_reference`, `exact`/`approx`) :
:                                      : |                                     :
| Text in a frame named `Principles`   | Candidate rules, quoted word for word |

### What it ignores or sends elsewhere

-   **Styling never goes into the constitution.** Colors, corner radii, fonts,
    type sizes, and spacing go to **theme tokens**. If the model notices styling
    it can't map to a token, it lists it as *out of scope* so you can decide
    what to do with it.
-   **Component behavior** (how a Slider drags, what a Modal does) belongs to
    the **catalog**, not to rules.
-   Marketing copy, sample names, and stock photos are treated as placeholder
    content, not as rules.
-   **Formatting problems** (wrong frame sizes, outlined text, cropped
    screenshots) come back as `formatting_feedback` so you can fix the input and
    run the analysis again.

> **Why the split matters:** a constitution that says "cards have 24px corners"
> fights the theme the first time someone changes it. A constitution that says
> "group a dose's name, schedule, and refill action in one Card" keeps working
> whatever the theme. Rules describe *decisions*, tokens describe *looks*.

## 2. Choosing what to send

The generator works with **varied input data**: one item or twenty, a pharmacy
order or a calendar day. Your references should show the *range* it has to
handle, not one perfect hero screen.

### Send a small, representative set

-   **3–8 screens.** More screens dilute the signal and use up the analyzer's
    attention.
-   **At least 2 data shapes or genres**, for example a single record (order
    details) *and* a collection (order history). Rules that hold for both are
    the ones worth writing down.
-   **States for each key screen**: Content, Loading, Empty, Error. The
    constitution has to say what happens when data is missing. If you don't show
    it, the model will make it up.
-   **Density extremes** where they matter: **XS** (1 item) and **XL** (20+
    items). These teach density caps such as "show at most 3 rows, then a 'See
    all' Button", the same kind of caps the Cosmo constitution uses (≤3 list
    items, ≤4 key-value pairs, ≤3 footer chips).
-   **At least one viewport besides phone** if your product runs on more than
    one device. The watch (192×192) and smart display (1024×600) layouts teach
    the most about priority, because space is so tight.
-   **At least one Don't** if you have one: a layout you've rejected and a line
    explaining why. One clear Don't often teaches more than three Dos.

### Don't send

| Avoid                                | Why                                   |
| ------------------------------------ | ------------------------------------- |
| Marketing comps / hero shots         | Polished one-offs with fake density.  |
:                                      : Rules learned from them break on real :
:                                      : data.                                 :
| Mood boards, illustration sheets     | No layout decisions in them, only     |
:                                      : styling the analyzer will route away. :
| A full 40-screen app flow            | The signal gets buried. Pick the 3–8  |
:                                      : screens that show the decisions.      :
| Desktop web pages built for a mouse  | Only useful if *desktop* is a target  |
:                                      : viewport.                             :
| Design-system spec sheets (redlines) | Tokens come through better from Figma |
:                                      : styles and variables.                 :
| Screens with real personal data      | Privacy. Use realistic fake data.     |

## 3. Figma

### Frames and sizes

Draw each screen as a **top-level frame** at one of the A2UI Designer viewport sizes. The
analyzer works out the viewport from the frame width and height.

Viewport      | Frame size (px) | Notes
------------- | --------------- | ----------------------------------------------
Phone         | 412 × 915       | Default. Content may scroll past 915.
Tablet        | 834 × 1194      | Portrait.
Desktop       | 1280 × 800      |
Smart display | 1024 × 600      | Landscape, glanceable, little or no scrolling.
Watch         | 192 × 192       | One idea per screen.

-   One screen per frame. Don't nest several screens inside one big frame.
-   Taller-than-viewport phone frames are fine for scrolling content. Keep the
    width exact.
-   Leave out device bezels, status bars, and keyboards. The renderer doesn't
    draw them, so the model shouldn't learn them.

### Naming: `Flow / Screen / State @ Viewport`

```
Refill / Results / Content @ Phone
Refill / Results / Empty @ Phone
Refill / Results / Content XL @ Tablet
Refill / Confirm / Error @ Watch
```

-   **States:** `Content`, `Loading`, `Empty`, `Error`.
-   **Density variants:** add `XS` (1 item) or `XL` (20+ items) after the state.
-   **Don'ts:** start the name with `Don't –`, e.g. `Don't – Refill / Results /
    Content @ Phone`, and put the reason in the frame description or a text
    layer next to it (see [Annotating intent](#5-annotating-intent)).

Good names let the analyzer compare matching frames (Content vs. Empty, Phone
vs. Watch) and write rules about the *difference*. That's where the decisions
are.

### Build it so the data can be read

| Do                                   | Why it helps                          |
| ------------------------------------ | ------------------------------------- |
| Use **auto layout** for stacks,      | Spacing is read as exact numbers      |
: rows, and lists                      : (`space`, `card_padding`). Rows and   :
:                                      : columns map cleanly to                :
:                                      : `Row`/`Column`/`List`.                :
| Keep text as **live text layers**    | The analyzer reads text styles and    |
:                                      : content. Outlined text is just a      :
:                                      : shape to it.                          :
| Apply **color and text styles** (or  | Values come back `exact` and with     |
: **variables**)                       : role names (`primary`,                :
:                                      : `surface_container`) instead of loose :
:                                      : hex codes.                            :
| **Name component instances by role** | Tells the model what each element is  |
: (`Dose card`, `Primary action`,      : *for*, which becomes "when to use"    :
: `Status chip`)                       : rules and catalog mapping.            :
| Use realistic, varied sample data    | Long names, missing fields, and big   |
:                                      : numbers show how the layout copes.    :
| Keep hidden and old layers out of    | Hidden layers still show up in the    |
: shared frames                        : node tree and add noise.              :

**Variables** (Enterprise plans) give the best token mapping. If you only have
styles, name them semantically (`Surface/Container`, `On surface/Variant`,
`Radius/Card`) rather than by value (`Gray 100`, `12px`).

### The `Principles` frame

Add a frame named exactly **`Principles`** with one text layer and **one rule
per line**:

```
Lead with the next action, not the history.
Never show more than 3 refills before "See all".
Errors explain what to do next in one sentence.
Watch: one number, one action.
```

Each line is quoted as a candidate rule. Keep lines short, concrete, and about
layout or content. Leave out styling: "Use brand blue" belongs in tokens.

### Light and dark

Put light and dark versions on **separate pages** (e.g. `Light`, `Dark`) with
the same frame names. The analyzer uses them to propose the `scheme` token and
the matching color roles. Send dark only if you're actually specifying dark.

### Selecting a file and frames (or sharing a link)

1.  Open **Imports → New import** (or click **From reference** in the
    Constitution or Design system toolbar).
2.  Pick one of your saved Figma files, project folders, or teams in the **Your
    Figma files** picker, then select the page and top-level frames or sections
    (up to 20 per import) in the frame checklist.
3.  Alternatively, select specific frames (or a section holding them) in Figma,
    right-click **Copy link to selection** (so the URL contains `?node-id=…`),
    and paste the link into the Figma tab.

Linking to a whole page or file without picking frames makes Figma render the
entire canvas as one huge, mostly empty image with tiny screens. Either use the
in-app frame checklist or link directly to selected frames (`?node-id=`).

### Personal access token

A2UI Designer reads Figma through the REST API with your personal access token:

1.  Figma → **Settings → Security → Personal access tokens → Generate new
    token**.
2.  Give it **read-only** access to **File content**. No other scopes are
    needed.
3.  Paste it into A2UI Designer (profile → Figma token). The token is stored **on the A2UI Designer
    server**. It's never sent back to the browser or included in prompts. Clear
    it at any time by saving an empty value.

You need view access to the file. Tokens expire, so if imports suddenly fail
with 403, make a new one.

### Common problems

| Symptom                 | Likely cause            | Fix                      |
| ----------------------- | ----------------------- | ------------------------ |
| One huge, mostly empty  | Link points to a page   | Use **Copy link to       |
: render                  : or file, not to frames  : selection** on the       :
:                         :                         : frames (`?node-id=`).    :
| Every token is `approx` | Hard-coded hex and px   | Apply color and text     |
:                         : values, no styles or    : styles (or variables)    :
:                         : variables               : and run it again.        :
| Wrong viewport detected | Frame width isn't a A2UI Designer | Resize the frame to the  |
: / `unknown`             : viewport size           : sizes above, or name it  :
:                         :                         : `@ Phone` etc.           :
| Text missing from       | Text was outlined or    | Keep live text layers.   |
: observations            : flattened into an image :                          :
| Spacing rules look      | No auto layout;         | Wrap stacks and rows in  |
: vague                   : elements placed by hand : auto layout.             :
| Components called       | Instances and layers    | Rename to roles (`Dose   |
: "Frame 238"             : not named by role       : card`, `Primary          :
:                         :                         : action`).                :
| 403 / "no access"       | Token expired, wrong    | Make a new token with    |
:                         : scope, or no access to  : File content (read);     :
:                         : the file                : check file sharing.      :
| 404 / file not found    | Branch, draft, or       | Copy the link again from |
:                         : mistyped URL            : the main file.           :
| Analysis dominated by   | Too many near-duplicate | Trim to 3–8 frames with  |
: one screen              : frames                  : real differences.        :

## 4. Screenshots

Use screenshots when you have no Figma file, e.g. shipped products, competitors,
or prototypes in other tools. You can upload up to **20 images per import**, but
3–8 is usually better.

### Format

-   **PNG**, at **1×–2× device resolution** (a phone at 412 or 824 px wide).
-   **Full screen, uncropped.** Cropping hides what's above and below, and the
    analyzer can't tell a crop from a whole screen.
-   **No bezels, browser chrome, or status/navigation bars.** Crop those off at
    the screen edge, not inside the content.
-   **No drawn annotations** (arrows, circles, red boxes). Put intent in the
    caption. Markup gets read as UI.
-   **One screen per file.** No side-by-side collages.
-   **Light theme** unless you're specifically specifying dark. Don't mix themes
    within one import.
-   **Redact personal data** by replacing it with realistic fake values. Avoid
    blur boxes, which read as images.
-   Prefer **content-led screens** over photo-heavy marketing: a full-bleed
    photo tells the analyzer almost nothing about layout.

### File names

Use the same pattern as Figma frames: `refill_results_content_phone.png`,
`refill_results_empty_phone.png`, `dont_refill_results_content_phone.png`.

### Captions (fill in every one)

```
Screen: Refill results, list of active prescriptions
Data: 3 prescriptions, one due today, one out of refills
Do or Don't: Do
Why: Due-today item is pinned first with its action inline; others collapse to one line.
```

Also pick a **viewport hint** for each image: phone, tablet, desktop, smart
display, or watch. Leave it on *auto* and the builder guesses from the pixel
size (allowing 2×–4× screenshots); choose *unknown* only for devices that fit
none of these, and name the device in the caption. The caption's **Why** line is
the most valuable thing you write, because the analyzer uses it to tell a
deliberate rule from an accident.

## Stitch

[Stitch](https://stitch.withgoogle.com) projects import the same way as Figma:
pick a project from **Your Stitch projects** (or paste the project link), choose
screens in the checklist, and the builder brings in each screen's image plus its
HTML structure and the project's **design system**. Everything in sections 2, 3
(naming, states, density variants) and 5 applies unchanged; this section only
covers what is different.

### API key

Create a key in Stitch → **Settings → API keys**, then paste it on the Stitch
tab of the import drawer (it is stored on the A2UI Designer server and never shown again).
A key that stops working shows up as "Stitch rejected the API key" — create a
new one.

### Set up the design system first

The design system (color mode, seed and override colors, headline / body / label
fonts, roundness, typography, spacing, DESIGN.md) is read as the designer's
declared intent, so token proposals taken from it are marked **exact**. Without
one, colors and radii are read from each screen's HTML and mostly come back
**approx**. Before importing:

-   Create or update the project's design system in Stitch and apply it to the
    screens you will send.
-   Put layout intent that is not styling ("one prescription per card", "never
    more than 3 actions") in the DESIGN.md. It is passed to the analyzer as
    candidate rules, like a Figma `Principles` frame.

### Choosing screens

-   Name screens like Figma frames: `Flow / Screen / State @ Viewport`.
-   Use Stitch variants to cover Content / Loading / Empty / Error and XS / XL
    densities instead of sending near-duplicates of one state.
-   Generate screens with the device type you mean (mobile, tablet, desktop);
    the builder uses it as the viewport hint.
-   The picker preselects every screen; untick explorations you did not keep. At
    most 20 per import.

### Common problems

| Symptom               | Cause                   | Fix                        |
| --------------------- | ----------------------- | -------------------------- |
| "Stitch rejected the  | Key revoked, mistyped,  | Create a new key in Stitch |
: API key"              : or from another account : → Settings                 :
| "Project not found or | Link to someone else's  | Ask the owner to share it  |
: permission denied"    : private project         : with you, or import from   :
:                       :                         : your own copy              :
| Frame says "rendered  | Stitch blocked the      | Nothing to do: the         |
: from HTML"            : screenshot download     : screen's HTML was rendered :
:                       : (known issue)           : instead; it can differ     :
:                       :                         : slightly from Stitch's     :
:                       :                         : preview                    :
| Frame shows an error  | Both downloads were     | Re-import later, or export |
: and no image          : blocked                 : the screen as PNG and add  :
:                       :                         : it as a screenshot         :
| All tokens **approx** | No design system on the | Add one in Stitch and      |
:                       : project                 : re-import                  :

## 5. Annotating intent

The analyzer can see *what* you drew. It can't always tell *why*. Tell it.

| You want to say…        | In Figma                | In screenshots          |
| ----------------------- | ----------------------- | ----------------------- |
| "Do this"               | Normal frame name       | `Do or Don't: Do`       |
| "Never do this"         | Frame name starts with  | `Do or Don't: Don't` +  |
:                         : `Don't –`, reason in    : `Why\:`                 :
:                         : frame description       :                         :
| "This is a rule"        | Line in the             | `Why:` states the rule  |
:                         : `Principles` frame      : plainly                 :
| "This is incidental,    | Frame description:      | Caption: `Ignore: hero  |
: don't learn it"         : `Incidental\:           : photo, copy`            :
:                         : illustration, sample    :                         :
:                         : copy`                   :                         :
| "This matters most"     | Order lines in          | Prefix `Why:` with      |
:                         : `Principles` by         : `MUST` / `PREFER`       :
:                         : priority; prefix `MUST` :                         :
:                         : / `PREFER`              :                         :
| "Only on this viewport" | `@ Watch` in the name + | Viewport hint + caption |
:                         : a Principles line       :                         :
:                         : `Watch\: …`             :                         :

Tips:

-   **One idea per annotation.** "Pin due items first" is a rule. "Make it feel
    calm and trustworthy" isn't something the generator can check.
-   **Say the condition.** "When there are more than 3 items, use a List with
    'See all'" is better than "use a List".
-   **Pair Dos and Don'ts** on the same data where you can. The difference
    between the two is the rule.
-   Use **MUST** for things that must never break (safety, errors, primary
    action) and **PREFER** for defaults the model may adapt to the content.

## 6. What you get back and how to review it

Every import opens in **Imports** (`/p/:pid/imports/:iid`) and progresses
through four stages before changing your project:

1.  **Ingest & Analyze** (with live step and crop progress): Gemini splits your
    frames or screenshots into region-cropped **extractions** and classifies
    each one as:
    -   **Reusable component (`component`)**: multi-part UI cards, list rows,
        headers, bars, or widgets. Composite patterns are drafted as custom A2UI
        components and automatically activated once clean; bare atomic
        primitives map to Basic components.
    -   **Template (`template`)**: a screen or section composed from components.
        Templates are saved to **Design system → Templates** (fed into the
        generator prompt under `# Templates` and exported in `package.zip`) and
        inform constitution layout rules.
    -   **Token / style reference (`token_reference`)**: palettes, type ramps,
        spacing/radius sheets, or restyled Basic primitives (`exact` from Figma
        styles/variables or a Stitch design system; `approx` from pixels).
    -   **Ignore (`ignore`)**: sample content, device chrome, or annotations.

    Figma frames and Stitch screens also yield **project data**: the text and
    content on each screen become sample drafts you can keep as a project sample
    set (and make the default data for runs).
2.  **Review & Questions**: any extraction with low confidence or ambiguous
    intent shows a clarifying question at the top of the extraction board. You
    can answer questions, reclassify extractions, merge duplicates, or drag on
    any source image to add a custom region.
3.  **Merge plan**: once all questions are answered, click **Prepare merge
    plan**. A2UI Designer groups the planned changes by destination (**Tokens**,
    **Components**, **Templates**, **Data**, and **Constitution** diff) so you
    can toggle individual lines or edit the proposed constitution text before
    clicking **Apply**. If your material only defines some colors, A2UI Designer fills the
    remaining color roles with matching, contrast-safe Material 3 colors (marked
    as derived); typefaces and weights from Figma or Stitch are filled in too.
4.  **Record & Chat summary**: applying the plan updates your project, records
    links from each extraction to the token, component, template, data sample,
    or constitution revision it produced, and posts a short summary in Chat.

### Reviewing

1.  **Answer questions and check extraction kinds first.** If a full screen was
    classified as a `component` instead of a `template`, switch it to `template`
    before preparing the merge plan.
2.  **Inspect the constitution diff in the merge plan.** Edit rules into your
    own words before applying, and delete any rule that only describes one
    screen.
3.  **Check each rule against your other data shapes.** If it only makes sense
    for the pharmacy screens, scope it ("For order lists…") or drop it.
4.  **Watch for styling that slipped in.** A rule that mentions px, hex, or font
    names belongs in tokens.
5.  **Accept `exact` tokens freely and `approx` tokens carefully.**
6.  **Uncheck any merge-plan line** you don't want applied yet (for example,
    apply tokens and components now and leave constitution rules for later).

### Iterating

-   **Run the new revision against your samples** and look at every viewport,
    especially watch and smart display.
-   **Compare** the new run with the previous revision side by side.
-   Run an **effect study** to check that the new rules change what you meant
    them to change and nothing else.
-   When a run gets it wrong, save the screenshot as a **Don't** with a reason.
    That feeds the next round better than a new import.
-   Re-analyze with short instructions when you want Gemini to split or
    reclassify regions differently, or draw a box directly on a source image to
    add a custom extraction.

## 7. Pre-flight checklist

Copy this into your file description or ticket:

```markdown
- [ ] 3–8 screens, covering at least 2 data shapes or genres
- [ ] Content, Loading, Empty, and Error states for each key screen
- [ ] XS (1 item) and XL (20+ items) variants where density matters
- [ ] At least one non-phone viewport (if the product needs it)
- [ ] At least one Don't, with a reason
- [ ] Frames at exact A2UI Designer sizes; one screen per frame
- [ ] Names follow `Flow / Screen / State @ Viewport`
- [ ] Auto layout on stacks, rows, and lists; live (not outlined) text
- [ ] Colors, text, and radii bound to styles or variables, named by role
- [ ] Component instances named by role, not "Frame 238"
- [ ] `Principles` frame: one short layout rule per line, MUST/PREFER marked
- [ ] Link copied from the selection (`?node-id=` present); token is read-only
- [ ] Screenshots: PNG, uncropped, no chrome or markup, captions filled in
- [ ] Stitch: design system set up and applied; screens named and device types right
- [ ] No real personal data; no marketing comps or mood boards
```

## 8. Worked examples (illustrative)

### A. Figma: pharmacy refill flow

**Input.** One Figma section linked with `?node-id=`, 7 frames plus
`Principles`:

```
Refill / Results / Content @ Phone        (3 prescriptions, one due today)
Refill / Results / Content XL @ Phone     (24 prescriptions)
Refill / Results / Empty @ Phone
Refill / Results / Error @ Phone
Refill / Confirm / Content @ Watch
Refill / Results / Content @ Smart display
Don't – Refill / Results / Content @ Phone (every item as a full card; reason: buries the due item)
Principles:
  MUST Lead with what is due today and its action.
  PREFER at most 3 items before "See all".
  Errors say what happened and the one thing to do next.
```

Colors and type use published styles; spacing uses auto layout; radii are
literal values.

**Excerpt of the proposal:**

```markdown
## Refill lists
- MUST: Put the item due soonest first, in its own Card with name, due date,
  and a primary "Refill" Button inside the Card.
- Show at most 3 items; beyond that, collapse the rest into a List of one-line
  rows and end with a "See all (N)" Button.
- Never render every item as a full Card (see Don't: buries the due item).
- Empty: one Text explaining there's nothing to refill, plus one Button to add a
  prescription. No illustration.
- Error: one sentence on what failed plus a single retry Button; keep any
  cached items visible below it.

## Watch
- One prescription, one number (days left), one Button. No List.
```

Token               | Value     | Confidence
------------------- | --------- | ---------------------------------
`primary`           | `#0B6E4F` | exact (style `Primary`)
`surface_container` | `#EEF5F1` | exact (style `Surface/Container`)
`font_family`       | `Inter`   | exact (text style)
`card_padding`      | `16`      | exact (auto layout padding)
`radius_card`       | `16`      | approx (literal radius, no style)

Components & Templates in the merge plan: draft custom component `DoseProgress`
(auto-activated in **Design system → Components** once drafted cleanly) and save
template `RefillResults` to **Design system → Templates**.

### B. Screenshots: calendar app (4 images)

**Input.** Four PNGs from a shipped calendar app, light theme, with captions:

| File                         | Viewport hint | Caption (abridged)            |
| ---------------------------- | ------------- | ----------------------------- |
| `day_content_phone.png`      | phone         | Data: 5 events. Do. Why: next |
:                              :               : event pinned on top with time :
:                              :               : remaining; others as rows.    :
| `day_empty_phone.png`        | phone         | Data: 0 events. Do. Why: "No  |
:                              :               : events" plus one "Create      :
:                              :               : event" action, nothing else.  :
| `week_content_tablet.png`    | tablet        | Data: 31 events over 7 days.  |
:                              :               : Do. Why\: Tabs per day        :
:                              :               : instead of one long list.     :
| `dont_day_content_phone.png` | phone         | Don't. Why: all-day events    |
:                              :               : mixed into timed rows, so the :
:                              :               : next meeting is hard to find. :

**Excerpt of the proposal:**

```markdown
## Schedules
- Pin the next upcoming event in a Card: title, time range, and time remaining.
- List the remaining events as one-line rows ordered by start time.
- Group all-day events above timed events, never mixed in.
- More than one day of data on tablet/desktop: use Tabs, one per day.
- Empty: a single Text plus one primary Button to create an event.
```

Token            | Value     | Confidence
---------------- | --------- | ----------
`primary`        | `#1A73E8` | approx
`surface`        | `#FFFFFF` | approx
`radius_control` | `20`      | approx
`scheme`         | `light`   | approx

Formatting feedback (medium): "`week_content_tablet.png` includes the status
bar; crop it at the screen edge." Out of scope: "Colored event stripes. Use the
`primary` token or leave this to theming."

> All tokens are `approx` because screenshots only give pixels. To get `exact`
> values, import the Figma file or edit the values in **Design system →
> Tokens**.
