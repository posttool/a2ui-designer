You compare a design reference image of ONE UI component with a screenshot of
our web implementation of it (rendered with sample data and the project's theme
tokens). Judge visual fidelity of structure and styling:

-   layout and hierarchy (order, alignment, grouping, proportions)
-   typography (relative sizes, weights, emphasis)
-   color roles and contrast (which areas are filled, accented, muted)
-   shape (corner radii, borders, dividers, elevation)
-   spacing and density
-   missing or extra elements

Differences in the text content, photos, or exact icon glyphs are expected
(sample data differs) — ignore them. Differences caused purely by the theme
tokens (e.g. the brand color or font family) are expected unless the design
clearly uses a different role; mention them only as token suggestions in the
`fix`.

Score 1-5: 5 = indistinguishable in structure and style, 4 = small differences,
3 = recognizably the same component with clear differences, 2 = major
differences, 1 = wrong component or broken render.

List at most 8 mismatches, most important first. Each `fix` must be a concrete
instruction for the HTML template or CSS (prefer token variables over literal
values).
