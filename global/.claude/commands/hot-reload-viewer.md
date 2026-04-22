---
description: Start the SVG wireframe viewer with hot reloading at localhost:3000 (user)
---

Start the interactive wireframe viewer with live reload using Docker.

## Instructions

```bash
cd docs/design/wireframes && docker compose up
```

This starts `live-server` in a container on port 3000 with automatic hot reloading when SVG files change.

## Viewer Features

- **Navigation**: Click sections in sidebar or use Left/Right arrow keys
- **Zoom**: Up/Down arrows or +/- keys (0 resets to 85%)
- **Hot reload**: Changes to SVGs auto-refresh in browser

## URL

Open http://localhost:3000 in your browser.

## Stop

Press `Ctrl+C` in the terminal to stop the container.
