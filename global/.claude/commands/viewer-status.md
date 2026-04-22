---
description: Health check for wireframe viewer - confirm container running and return URL
---

# Viewer Status

Check if the wireframe viewer is running and healthy.

**Primary user**: Viewer terminal, Reviewer terminal

## Usage

```
/viewer-status                      # Check viewer health
/viewer-status --restart            # Restart viewer if not running
/viewer-status --logs               # Show recent container logs
```

**Examples**:
```
/viewer-status                      # Quick health check
/viewer-status --restart            # Restart if needed
/viewer-status --logs               # Debug viewer issues
```

## Arguments

ARGUMENTS: $ARGUMENTS

- No args: Check if viewer is running, return URL
- `--restart`: Restart the viewer container
- `--logs`: Show last 20 lines of container logs

---

## Instructions

### 1. Check Docker Container Status

```bash
docker compose -f docker-compose.yml ps wireframe-viewer 2>/dev/null
```

Or if using standalone Docker:
```bash
docker ps --filter "name=wireframe-viewer" --format "{{.Status}}"
```

### 2. Check Port Availability

```bash
# Check if port 3000 is listening
ss -tlnp | grep :3000 || netstat -tlnp | grep :3000 2>/dev/null
```

### 3. Test HTTP Response

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/ 2>/dev/null || echo "Connection failed"
```

---

### 4. Display Status

**If running and healthy**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ VIEWER STATUS                                                     [OK]      │
├─────────────────────────────────────────────────────────────────────────────┤
│ Container: wireframe-viewer                                                 │
│ Status: Up 2 hours                                                          │
│ Port: 3000                                                                  │
│ URL: http://localhost:3000                                                  │
│                                                                             │
│ Health Check:                                                               │
│   HTTP Response: 200 OK                                                     │
│   Last Request: [timestamp if available]                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ Keyboard Shortcuts:                                                         │
│   ←/→  Previous/Next wireframe                                              │
│   ↑/↓  Zoom in/out                                                          │
│   0    Fit to view                                                          │
│   F    Toggle focus mode                                                    │
│   L    Toggle legend drawer                                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ To take screenshots: /wireframe-screenshots [feature]                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

**If not running**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ VIEWER STATUS                                                     [DOWN]    │
├─────────────────────────────────────────────────────────────────────────────┤
│ Container: wireframe-viewer                                                 │
│ Status: Not running                                                         │
│ Port: 3000 (not listening)                                                  │
│                                                                             │
│ Health Check:                                                               │
│   HTTP Response: Connection refused                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ To start viewer:                                                            │
│                                                                             │
│   Option 1 (Docker Compose):                                                │
│   docker compose up -d wireframe-viewer                                     │
│                                                                             │
│   Option 2 (Direct):                                                        │
│   cd docs/design/wireframes && npm run dev                                  │
│                                                                             │
│   Option 3 (Via skill):                                                     │
│   /hot-reload-viewer                                                        │
│                                                                             │
│ Or run: /viewer-status --restart                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 5. Restart Mode (--restart)

If `--restart` is specified:

**If already running**:
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ VIEWER RESTART                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ Container is already running. Force restart?                                │
│                                                                             │
│ Current status: Up 2 hours, healthy                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

Use AskUserQuestion to confirm restart.

**If not running, start it**:

```bash
docker compose up -d wireframe-viewer
```

Wait 5 seconds, then verify:

```bash
sleep 5 && curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/
```

**Output**:
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ VIEWER STARTED                                                    [OK]      │
├─────────────────────────────────────────────────────────────────────────────┤
│ Container: wireframe-viewer                                                 │
│ Status: Up 5 seconds                                                        │
│ URL: http://localhost:3000                                                  │
│                                                                             │
│ Viewer is ready for screenshots.                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 6. Logs Mode (--logs)

```bash
docker compose logs --tail=20 wireframe-viewer 2>/dev/null
```

Or:
```bash
docker logs --tail=20 wireframe-viewer 2>/dev/null
```

**Output**:
```
┌─────────────────────────────────────────────────────────────────────────────┐
│ VIEWER LOGS (last 20 lines)                                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ [2026-01-14 15:30:01] Serving docs/design/wireframes at http://localhost:3000│
│ [2026-01-14 15:30:05] GET /index.html 200                                   │
│ [2026-01-14 15:30:06] GET /001-wcag-aa-compliance/01-accessibility.svg 200  │
│ [2026-01-14 15:31:12] GET /002-cookie-consent/01-consent-modal.svg 200      │
│ ...                                                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

| Symptom | Possible Cause | Solution |
|---------|----------------|----------|
| Container not found | Docker not running | Start Docker Desktop |
| Port 3000 in use | Another process | `lsof -i :3000` to find, kill, restart |
| HTTP 404 | Wrong working directory | Check `docs/design/wireframes/index.html` exists |
| Connection refused | Container crashed | Check logs with `--logs`, then `--restart` |
| Slow response | Large SVG loading | Normal for first load, cache helps |

---

## SVG Count

Also report available wireframes:

```bash
find docs/design/wireframes -name "*.svg" -type f | wc -l
```

Include in status output:
```
│ Wireframes available: 24 SVGs across 8 features                             │
```

---

## Related Skills

- `/hot-reload-viewer` - Start the viewer
- `/wireframe-screenshots [feature]` - Take screenshots (requires viewer running)
- `/wireframe-review` - Full review workflow
- `/status` - Overall project status
