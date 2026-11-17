/* Shared Markdown dialect for desktop rendering, previews and speech. */
(function (global) {
  'use strict';
  const commonjs = typeof module !== 'undefined' && module.exports;
  const marked = commonjs ? require('../vendor/markdown/marked.umd.js') : global.marked;
  marked.use({
    tokenizer: {
      del(src) {
        // Single tildes commonly separate numeric ranges. Only explicit double
        // tildes delegate to GFM; returning undefined prevents single-tilde fallback.
        if (src.startsWith('~~')) return false;
      },
    },
  });
  if (commonjs) module.exports = marked;
})(globalThis);
