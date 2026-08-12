/*
 * Copyright (c) Hervé Le Roy and contributors.
 * SPDX-License-Identifier: MIT
 *
 * Part of django-altcha-widget, not of upstream ALTCHA.
 *
 * Registers the ALTCHA Proof-of-Work workers for the modular "external" build.
 *
 * The default ALTCHA bundle inlines its workers and instantiates them from a
 * `blob:` URL, which requires `worker-src blob:` in the Content-Security-Policy.
 * django-altcha-widget serves the external build instead, which ships no workers
 * at all and registers no algorithm, so the integrator has to point
 * `$altcha.algorithms` at plain worker scripts served from the same origin.
 *
 * This module must be loaded *after* `external/altcha.min.js`, which creates the
 * `$altcha` global. Both are `type="module"` scripts without `async`, so the
 * browser evaluates them in document order.
 *
 * Worker URLs are passed in as a JSON mapping of file name to URL, through the
 * `data-altcha-workers` attribute. The workers are vendored alongside the rest of
 * ALTCHA, but resolving their URLs server-side through staticfiles is what keeps
 * them correct under a hashed storage. The mapping is always rendered, so its
 * absence means this module was loaded by something other than the widget.
 */
const ALGORITHMS = {
  "SHA-256": "sha.js",
  "SHA-384": "sha.js",
  "SHA-512": "sha.js",
  "PBKDF2/SHA-256": "pbkdf2.js",
  "PBKDF2/SHA-384": "pbkdf2.js",
  "PBKDF2/SHA-512": "pbkdf2.js",
  ARGON2ID: "argon2id.js",
  SCRYPT: "scrypt.js",
};

function getWorkerUrls() {
  const script = document.querySelector("script[data-altcha-workers]");
  if (!script) {
    throw new Error(
      "No data-altcha-workers mapping found. altcha-workers.js is rendered by " +
        "django-altcha-widget, which resolves the worker URLs server-side.",
    );
  }
  try {
    return JSON.parse(script.dataset.altchaWorkers);
  } catch {
    throw new Error("Unable to parse the data-altcha-workers mapping.");
  }
}

const workerUrls = getWorkerUrls();
const altcha = globalThis.$altcha;

if (!altcha || !altcha.algorithms) {
  throw new Error(
    "ALTCHA is not loaded. altcha-workers.js must be loaded after altcha.min.js.",
  );
}

for (const [algorithm, filename] of Object.entries(ALGORITHMS)) {
  const url = workerUrls[filename];
  if (url) {
    altcha.algorithms.set(algorithm, () => new Worker(url));
  }
}
