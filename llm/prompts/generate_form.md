You are building the INPUT FORM a person fills in before a connected app's tool
runs (like Gemini asking for the details a tool needs). Follow the Design
Constitution above for structure, hierarchy, grouping and density exactly as you
would for any other surface; it governs forms too.

Requirements (these make the form usable by the host and are not negotiable):

-   One surface. Bind every field's value to the data model at `/form/<field>`
    using the field names from the tool's JSON Schema below (e.g. `{"path":
    "/form/query"}`). Initialize `/form` in an `updateDataModel` message with
    the prefilled values given below (booleans as true/false, enum fields and
    arrays as lists of strings, numbers as numbers, other fields as strings).
-   Input components by schema type: string → TextField (`variant: "longText"`
    for long free text, `"number"` for numeric), boolean → CheckBox, enum →
    ChoicePicker (`mutuallyExclusive`; `multipleSelection` for arrays of enums),
    bounded integer/number → Slider or TextField `number`, `format: date` /
    `date-time` / `time` → DateTimeInput. Arrays of free strings → a TextField
    where values are comma-separated (say so in the label or a caption).
-   Exactly one primary Button that submits: `"action": {"event": {"name":
    "submit", "context": {"form": {"path": "/form"}}}}`. Label it with the
    tool's verb ("Search", "Request refill", "Find times").
-   Mark optional fields as optional; surface field descriptions as short
    captions only when they help. Do not invent fields that are not in the
    schema.
-   Use the title/description to frame the form briefly (who it is for, what
    will happen), without marketing copy.

Return a JSON object `{"messages": [...]}` with the A2UI v0.9 messages.
