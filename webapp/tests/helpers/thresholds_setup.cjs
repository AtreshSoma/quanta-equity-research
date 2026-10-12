/* Installs the decision thresholds for every unit test, the way the page gets
   them from the server: engine/thresholds.py, exported to the committed fixture
   tests/fixtures/thresholds.json (a Python test keeps the two equal; regenerate
   with `python -m engine.thresholds` from core/). Loaded through
   `node --require` so app.js reads it at module load. */
const fs = require('node:fs');
const path = require('node:path');

globalThis.__QUANTA_THRESHOLDS__ = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'thresholds.json'), 'utf8'));
