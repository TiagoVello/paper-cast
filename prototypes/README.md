# Prototypes — throwaway, kept as primary source

Neither of these is production code. They were written to answer one question
each, they answered it, and they live on this branch so the answers stay
inspectable. Nothing here should be promoted; rewrite it properly when it is
folded in.

Both were installed into a live Omarchy shell during the design session, by
copying the directory into `~/.config/omarchy/plugins/<id>/` and running
`omarchy plugin enable <id> --section right`.

## `dnd-probe/` — can a bar icon receive a dropped file?

**Question:** can a Qt Quick `DropArea` on a Quickshell bar widget — living on a
`zwlr_layer_surface_v1`, not an xdg-toplevel — receive a drag that started in an
unrelated application?

Nobody had published an answer, for Quickshell or for any other wlroots bar or
dock. Static evidence went as far as "nothing forbids it": the bar's
`PanelWindow` (`Bar.qml:1234`) sets no `mask` and no `keyboardFocus`, Qt resolves
drag targets through `QWaylandWindow::fromWlSurface` without checking the shell
type, and Hyprland routes drag focus through ordinary pointer focus.

**Answer: it works.** Dragging from Nautilus produced

    ENTERED hasUrls=true formats=["application/x-gtk-local-dnd","text/plain",
      "text/uri-list","application/vnd.portal.filetransfer",
      "application/vnd.portal.files"]
    DROPPED count=2 urls=file:///…prosthetic….pdf, file:///…emg….pdf

so: a multi-file drag delivers **every** URL, paths with spaces survive intact,
and `text/uri-list` is offered directly — the portal file-transfer API is never
needed. The `WidgetButton`'s own `MouseArea` does not swallow drops, because Qt
delivers them via `ItemAcceptsDrops`, a separate path from mouse hit-testing.

Implemented for real in #16.

## `omarchy-panel/` — what should the dropdown look like?

**Question:** three structurally different takes on the panel, on the real
anchored bar surface with real theming, switchable in place with ← / → or the
footer bar. Everything is stubbed in `Stub.js`: a fake queue, a picker that
stages a fake filename, an "Add to queue" that mutates an in-memory array. No
disk, no NotebookLM, no upload.

- **A — Compose-first.** Steering is the hero; the queue is one status line.
- **B — One surface.** No modes. Sources left, Steering right, queue below as a
  real list with Retry and Open.
- **C — Tabbed.** *New episode* and *Queue*, neither cramped, one at a time.

**Answer: B**, plus a Steering carousel across the top that B did not originally
have — pre-written `--focus` texts flipped through rather than retyped, with
in-place rename and two buttons to add and delete. Editing the loaded text
rewrites that preset live.

The bar icon in the prototype also carries the settled icon design: glyph only,
no text ever, with colour as the entire state channel and a slow breath while a
Job runs.

Folded into #15.
