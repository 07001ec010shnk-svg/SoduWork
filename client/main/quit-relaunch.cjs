'use strict';

/*
 * A confirmed quit can take several seconds while running conversations stop.
 * A launch during that window cannot take the single-instance lock, so it hands
 * off to this process and exits; this process then exits too and nothing is
 * left running. Remember the request and start a fresh instance after exit.
 */
function create(app) {
  let quitting = false;
  let relaunchScheduled = false;
  return {
    markQuitting() { quitting = true; },
    get quitting() { return quitting; },
    // Returns true when the launch was deferred until this process exits.
    deferLaunch() {
      if (!quitting) return false;
      if (!relaunchScheduled) {
        relaunchScheduled = true;
        app.relaunch();
      }
      return true;
    },
  };
}

module.exports = { create };
