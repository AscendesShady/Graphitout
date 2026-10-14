# Graphitout

A single-file, offline editor for software architecture and algorithm diagrams.

**Two modes:** keep using the independent browser editor for initial ideas, or use the optional local MCP companion to connect a coding agent to any project folder. The standalone editor still needs no server. [MCP setup for Claude Code, VS Code, and other agents](docs/MCP_SETUP.md).

## Open it

Double-click **index.html**, or open it in a modern browser. That is the entire application: HTML, CSS, JavaScript, icons, and the starter diagrams are embedded. No server, build step, account, package installation, or network connection is needed.

## GitHub Pages

In the repository's **Settings → Pages**, choose **Deploy from a branch**, then **main** and **/ (root)**. Save the setting. The app's entry point is the root `index.html`; there is no build command or asset path to configure.

After GitHub finishes deploying, the project URL will be **https://ascendesshady.github.io/Graphitout/**. The app has no external runtime assets and works under this project path. When using the hosted site, browser autosave remains local to that browser; export JSON to share a diagram or keep a backup. Offline startup is supported by downloaded HTML copies; this version does not install a service worker for the hosted site.

## Plan a system

1. Drag a block from the library onto the canvas, or click a block type to add it.
2. Select a block to edit its name, type, and responsibility in the details panel.
3. Drag a connector dot to another block. Alternatively, choose **Connect** and click the source followed by its destination.
4. Select an arrow to label the communication, choose direct/event styling, change its ports, or reverse its direction.
5. Select a block to inspect its **Sends to** and **Receives from** lists. Click a neighbor to navigate to it.

Load **Software architecture**, **Algorithm flow**, or **Blank canvas** from the template picker. Replacing a canvas can be undone.

Drag empty space to pan; use the mouse wheel, pinch, or zoom buttons to zoom. **Fit** shows the complete diagram. Shift-click selects several blocks, which can be moved or duplicated together. Grid snapping can be toggled; holding Alt while dragging bypasses it.

On phones, use **+** to open the block library and the panel icon to edit the selection. The initial view focuses on the first block at a readable scale; use Fit for an overview.

## Keep your work

- **Browser autosave:** stores the latest diagram on this browser when storage is available. Clearing browser data removes this copy. File-URL storage behavior can differ between browsers; keep exported backups.
- **JSON:** the editable diagram, including names, notes, positions, and connections. Use Open or drop a JSON file onto the canvas to load it.
- **SVG:** a scalable diagram image. Long labels and notes are visually shortened; the complete block text is included in SVG titles. JSON retains all text.
- **Mermaid:** a flowchart description for tools that support Mermaid. It exports node names and edge labels; the receiving tool chooses its own layout.
- **Portable HTML:** a copy of the entire editor with the current diagram embedded. Each exported copy has independent browser autosave. Later edits stay in browser storage; export another copy to update the actual file.
- **Review pack:** the graph plus its code baseline, relative file/symbol references, source evidence, design decisions, and second-opinion notes. Take it to another agent or import it into the browser later.
- **Implementation brief:** Markdown showing the intended workflow, current architecture, and semantic changes from the code baseline.

Use **Workflow & agent review** to record goals, architecture decisions, and feedback. File and symbol references are optional while sketching. When project-connected, graph edits become a change brief that your coding agent can implement with its ordinary coding tools; moving blocks changes only the diagram layout.

## Shortcuts

| Action | Key |
| --- | --- |
| Select / connect / pan | V / C / H |
| Temporarily pan | Hold Space |
| Add a component | N |
| Fit diagram | F |
| Cancel / deselect | Escape |
| Select all | Ctrl+A |
| Duplicate selection | Ctrl+D |
| Delete selection | Delete / Backspace |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z |
| Save JSON backup | Ctrl+S |
| Nudge selected blocks | Arrow keys; Shift for larger steps |

Use Command instead of Ctrl on a Mac. Double-click a block or arrow to edit it; double-click empty space to add a component.

## Is one HTML file enough?

**Yes, for a personal diagram editor.** JavaScript handles the graph and interactions, CSS handles the interface, SVG draws the connections, and browser storage handles local persistence. All of these can live in one HTML file.

A backend would be useful for accounts, cloud synchronization, access permissions, and real-time collaboration. Splitting the source into modules would help maintain a much larger application, but it is not required to use this version. Graphitout plans software; it does not execute the algorithms or services in a diagram.

## Design reference

The interface adapts the visual language of [sdegenaar/liquid_glass_widgets](https://github.com/sdegenaar/liquid_glass_widgets): floating glass controls, capsule shapes, translucent surfaces, highlights from the upper left, subtle press feedback, and an animated selection indicator. Graph blocks and content rows remain opaque, following the reference's glass-for-controls approach.

This is an original CSS/JavaScript implementation. It uses browser backdrop blur and gradients rather than importing the Flutter package or reproducing its native shader/refraction pipeline. Everything remains embedded in the single HTML file. Reduced-motion preferences disable the animation; high-contrast and reduced-transparency preferences use solid control surfaces. Browsers without backdrop blur retain readable translucent panels.

Source references inspected: the repository README, `lib/constants/glass_defaults.dart`, `lib/theme/glass_interaction_settings.dart`, `lib/widgets/interactive/glass_button.dart`, the showcase theme, and component golden images. Development reference material under `qa/reference` is not required by the application.

## Verification

The scripts under `qa/` are development-only Playwright checks. They are not needed to run Graphitout. The checks cover graph editing, dragging, connections, undo/redo, relationship inspection, JSON import validation, all export formats, portable-copy persistence, offline startup, phone controls at 390/360px widths, unavailable browser storage, tablet layouts, the glass tool indicator, accessibility preferences, and the GitHub Pages project path.

To rerun the checks with Node.js installed:

```sh
npm install
npx playwright install chromium
npm test
```

To use an installed Chrome instead, set `GRAPHITOUT_BROWSER_CHANNEL=chrome` in your shell before running the tests. Screenshots and results are generated under `qa/output`. Generated output and downloaded design references are excluded from Git.
