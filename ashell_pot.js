// BotGuard driver for a-Shell's `jsc`. Run by ashell_pot.py, which concatenates
// bgutils.bundle.js in front of this file and injects the request as __ASHELL_POT_REQUEST.
//
// `jsc` evaluates this through wasmWebView.evaluateJavaScript, which returns as soon as the
// top-level statements finish -- it does not await promises. So nothing here can be returned
// to Python directly. Instead the result is written to a file and the caller polls for it.
// The webview keeps running timers and honours the jsc file bridge after `jsc` has exited,
// so the asynchronous tail of this script still completes.
//
// The page also outlives the Python process. A minted session is parked on globalThis and
// reused, which skips the expensive BotGuard run on all but the first call.

(function (request) {
  var SESSION_KEY = '__ashell_pot_session';

  function finish(result) {
    try {
      jsc.writeFile(request.result_path, JSON.stringify(result));
    } catch (e) {
      // Nothing left to report to; the caller times out instead.
    }
  }

  function nowSeconds() {
    return Math.floor(Date.now() / 1000);
  }

  // Fetch the challenge, run the BotGuard VM, trade the response for an integrity token, and
  // build a minter. This is the slow path: a network round trip plus VM execution.
  function createSession() {
    var signalOutput = [];

    return BgUtils.getChallenge({
      fetchFunction: fetch,
      requestKey: request.request_key,
    }).then(function (challenge) {
      var interpreter =
        challenge.interpreterJavascript &&
        challenge.interpreterJavascript.privateDoNotAccessOrElseSafeScriptWrappedValue;
      if (!interpreter) {
        throw new Error('challenge carried no interpreter javascript');
      }
      // Runs in global scope, which is where BotGuard installs itself under challenge.globalName.
      new Function(interpreter)();
      return BgUtils.BotGuardClient.create({
        program: challenge.program,
        globalName: challenge.globalName,
        globalObject: globalThis,
      });
    }).then(function (client) {
      return client.snapshot({ webPoSignalOutput: signalOutput });
    }).then(function (botguardResponse) {
      return fetch(BgUtils.buildURL('GenerateIT', false), {
        method: 'POST',
        headers: BgUtils.getHeaders(),
        body: JSON.stringify([request.request_key, botguardResponse]),
      });
    }).then(function (response) {
      if (!response.ok) {
        throw new Error('GenerateIT returned HTTP ' + response.status);
      }
      return response.json();
    }).then(function (json) {
      var ttl = json[1] || 3600;
      return BgUtils.WebPoMinter.create({
        integrityToken: json[0],
        estimatedTtlSecs: json[1],
        mintRefreshThreshold: json[2],
        websafeFallbackToken: json[3],
      }, signalOutput).then(function (minter) {
        var session = {
          minter: minter,
          // Retire the session a few minutes early rather than discover it expired mid-download.
          expires_at: nowSeconds() + ttl - 300,
        };
        globalThis[SESSION_KEY] = session;
        return session;
      });
    });
  }

  function currentSession() {
    var session = globalThis[SESSION_KEY];
    if (session && session.minter && session.expires_at > nowSeconds()) {
      return Promise.resolve(session);
    }
    return createSession();
  }

  function mint(session) {
    return session.minter.mintAsWebsafeString(request.content_binding).then(function (token) {
      return { ok: true, po_token: token, expires_at: session.expires_at };
    });
  }

  currentSession().then(mint).catch(function (error) {
    // A parked session can be rejected by the server long before its stated TTL. Drop it and
    // pay for a fresh BotGuard run once before giving up.
    delete globalThis[SESSION_KEY];
    return createSession().then(mint).catch(function (retryError) {
      return { ok: false, error: String((retryError && retryError.stack) || retryError) };
    });
  }).then(finish);

  return 'started';
})(__ASHELL_POT_REQUEST);
