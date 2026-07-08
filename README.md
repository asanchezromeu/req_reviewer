# ReqFind Raspberry Pi Showcase

ReqFind is a lightweight local requirements workspace built for an Ollama demo on a
Raspberry Pi 5. It provides:

- JSON and CSV import plus direct requirement editing.
- SQLite persistence in `backend/data/showcase.db`.
- Background embedding refresh after each save.
- Requirement mode: returns only the closest semantic match.
- Summary mode: retrieves the 10 closest requirements and asks a small local LLM for an
  executive summary.

The original ReqIQ analysis endpoints remain available in the backend, while the frontend
opens directly into the showcase workspace.

## Raspberry Pi setup

1. Install Ollama.
2. Pull the local models:

   ```bash
   ollama pull gemma3:1b
   ollama pull embeddinggemma
   ```

   `embeddinggemma` requires Ollama 0.11.10 or newer.

3. Install backend dependencies:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r backend/requirements.txt
   ```

4. Build the frontend:

   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

5. Start the API:

   ```bash
   uvicorn backend.server:app --host 0.0.0.0 --port 8000
   ```

6. Open `http://<raspberry-pi-address>:8000`. FastAPI serves the compiled frontend and API
   from the same process.

During development, run `npm start` in `frontend` and use port 3000.

## Configuration

- `OLLAMA_URL`: defaults to `http://localhost:11434`.
- `OLLAMA_MODEL`: defaults to `gemma3:1b`.
- `OLLAMA_EMBED_MODEL`: defaults to `embeddinggemma`.
- `SHOWCASE_DB_PATH`: overrides the local SQLite file.

The runtime settings can also be changed from the expandable settings section in the UI.

## Import format

JSON accepts a list or an object with a `requirements` list. CSV accepts `text`,
`requirement`, or `description` as the requirement column. Optional columns are `id` and
`source`.

## Tests

The showcase unit tests use only the Python standard library:

```bash
python -m unittest backend.tests.test_showcase_unit -v
```
