# Screenshots

Place project screenshots in this directory. Recommended filenames:

| Filename         | Description                                        |
|------------------|----------------------------------------------------|
| `upload.png`     | Upload page (drag & drop zone, file picker)        |
| `tasks-list.png` | Task list / history page                           |
| `processing.png` | Processing progress view (WebSocket live updates)  |
| `results.png`    | Results page with stems, MIDI player, analysis     |
| `mixer.png`      | Stem mixer with per-track volume / mute / solo     |
| `samples.png`    | Sample library management page                     |
| `dark-mode.png`  | Dark mode variant of the results page              |

## How to capture

1. Start the stack: `docker compose up --build`
2. Open http://127.0.0.1:8080
3. Register a test account (if `AUTH_REQUIRED=true`)
4. Upload a short audio clip (5-30s MP3/WAV)
5. Wait for processing to finish, then navigate through each page
6. Capture at 1440×900 viewport for consistency

After adding screenshots, reference them in `README.md` under the
"Screenshots" section using relative paths, e.g.:

```markdown
## Screenshots

![Upload page](docs/screenshots/upload.png)
![Results](docs/screenshots/results.png)
```
