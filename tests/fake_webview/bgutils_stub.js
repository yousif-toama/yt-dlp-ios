// Stands in for bgutils.bundle.js, which needs a real browser and Google's servers. It keeps
// the bundle's shape and asynchrony, so ashell_pot.js runs unchanged on top of it.
//
// Tests steer it through globals on the page, which outlive each `jsc` call:
//   __stub_challenges   how many BotGuard runs have started
//   __stub_fail         'challenge' or 'mint' fails that step once; 'always' fails every run
//   __stub_never        the promise chain never settles, like a BotGuard run that hangs

var fetch = function () {
  return later({ ok: true, status: 200, json: function () { return later(['integrity', 3600, 300, 'fallback']); } });
};

function later(value) {
  return new Promise(function (resolve) { setTimeout(function () { resolve(value); }, 20); });
}

function failing(step) {
  var fail = globalThis.__stub_fail;
  if (fail === 'always') return true;
  if (fail === step) {
    globalThis.__stub_fail = null;
    return true;
  }
  return false;
}

var BgUtils = {
  getChallenge: function () {
    globalThis.__stub_challenges = (globalThis.__stub_challenges || 0) + 1;
    if (globalThis.__stub_never) return new Promise(function () {});
    if (failing('challenge')) return Promise.reject(new Error('stub challenge failed'));
    return later({
      interpreterJavascript: { privateDoNotAccessOrElseSafeScriptWrappedValue: 'globalThis.__stub_vm = true;' },
      program: 'program',
      globalName: 'stubBotGuard',
    });
  },
  BotGuardClient: {
    create: function () {
      return later({ snapshot: function () { return later('botguard-response'); } });
    },
  },
  buildURL: function (name) { return 'https://example.invalid/' + name; },
  getHeaders: function () { return {}; },
  WebPoMinter: {
    create: function () {
      return later({
        mintAsWebsafeString: function (binding) {
          if (failing('mint')) return Promise.reject(new Error('stub mint failed'));
          return later('token-for-' + binding);
        },
      });
    },
  },
};
