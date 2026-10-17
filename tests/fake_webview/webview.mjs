// A stand-in for a-Shell's WKWebView, close enough to run ashell_*.py against on Linux.
//
// a-Shell's `jsc` hands a script to wasmWebView.evaluateJavaScript and prints the completion
// value. This server plays the webview: it holds one page, evaluates scripts in it as classic
// scripts, and keeps it -- with its timers, promises and globals -- alive between `jsc` calls,
// just as the real page outlives each command and the Python process. The `jsc` client next
// to this file talks to it over a Unix socket.
//
// What it models, and why each matters to the Python side:
//
// - wasm.html's globals: `const jsc` with writeFile/readFile, `println`, and console.log
//   rebound to println. Any other URL is a page without them, like youtube.com.
// - Assigning `location` on the page navigates it, asynchronously, as in a browser. Iframes
//   get their own global scope, so the same assignment there navigates nothing.
// - `history.back()` returns to the previous URL. `jsc --reset` reloads the current URL,
//   whatever it is, and returns before the reload has happened.
// - A page can be killed (stops answering until a reset), wedged (stops answering for good)
//   or frozen (answers, but ignores reloads), through the control op.
//
// Everything else a real page has -- layout, network, WebKit quirks -- is absent. See
// TESTING.md for the line between this and the device.

import fs from 'node:fs';
import net from 'node:net';
import vm from 'node:vm';

export const PAGE_URL = 'https://localhost:8443/wasm.html';
const NAVIGATION_DELAY_MS = 50;

const socketPath = process.argv[2];

const browserGlobals = () => ({
  URL,
  URLSearchParams,
  TextEncoder,
  TextDecoder,
  atob,
  btoa,
  structuredClone,
});

let page = null;
let mode = 'alive';
let backStack = [];
let loads = 0;

function makeTimers(owner) {
  return {
    setTimeout(fn, ms, ...args) {
      const handle = setTimeout(() => {
        owner.timers.delete(handle);
        fn(...args);
      }, ms);
      owner.timers.add(handle);
      return handle;
    },
    clearTimeout(handle) {
      owner.timers.delete(handle);
      clearTimeout(handle);
    },
  };
}

function makeFrame() {
  const element = {
    style: {},
    contentWindow: null,
    setAttribute() {},
    remove() {
      element.contentWindow = null;
    },
  };
  return element;
}

function makeDocument() {
  return {
    createElement(tag) {
      if (tag !== 'iframe') throw new Error(`fake webview: no <${tag}> elements`);
      return makeFrame();
    },
    documentElement: {
      appendChild(element) {
        const context = vm.createContext({ ...browserGlobals(), console: { log() {} } });
        element.contentWindow = vm.runInContext('globalThis', context);
        return element;
      },
    },
  };
}

function load(url) {
  if (page) {
    for (const handle of page.timers) clearTimeout(handle);
  }
  loads += 1;
  const owner = { url, timers: new Set(), output: null };
  const host = {
    location: () => new URL(owner.url),
    navigate: (target) => schedule(owner, () => go(new URL(target, owner.url).href)),
    back: () => schedule(owner, () => backStack.length && load(backStack.pop())),
    historyLength: () => backStack.length + 1,
    print: (text) => owner.output !== null && owner.output.push(String(text)),
    writeFile: (path, content) => fs.writeFileSync(path, String(content)),
    readFile: (path) => fs.readFileSync(path, 'utf8'),
  };
  owner.context = vm.createContext({
    ...browserGlobals(),
    ...makeTimers(owner),
    document: makeDocument(),
    __fakeWebviewHost: host,
  });
  vm.runInContext(
    `Object.defineProperty(globalThis, 'location', {
       get: () => __fakeWebviewHost.location(),
       set: (value) => __fakeWebviewHost.navigate(String(value)),
     });
     globalThis.history = {
       back: () => __fakeWebviewHost.back(),
       get length() { return __fakeWebviewHost.historyLength(); },
     };
     globalThis.console = { log() {}, info() {}, warn() {}, error() {}, debug() {} };`,
    owner.context,
  );
  if (url === PAGE_URL) {
    // wasm.html declares these at the top level of a classic script, so `jsc` is a global
    // lexical binding that a later `var jsc` collides with.
    vm.runInContext(
      `const jsc = {
         writeFile: (path, content) => __fakeWebviewHost.writeFile(path, content),
         readFile: (path) => __fakeWebviewHost.readFile(path),
       };
       function println(text) { __fakeWebviewHost.print(text); }
       console.log = println;`,
      owner.context,
    );
  }
  page = owner;
}

function go(url) {
  backStack.push(page.url);
  load(url);
}

// Only the page that started a navigation may complete it; a reload in between cancels it.
function schedule(owner, action) {
  setTimeout(() => page === owner && action(), NAVIGATION_DELAY_MS);
}

function answers() {
  return mode === 'alive' || mode === 'frozen';
}

function evaluate(source) {
  const current = page;
  current.output = [];
  try {
    const value = vm.runInContext(source, current.context, { filename: 'jsc-script.js' });
    const lines = current.output;
    if (['string', 'number', 'boolean'].includes(typeof value)) lines.push(String(value));
    else return { stdout: join(lines), stderr: 'Error: a result of an unsupported type\n', code: 1 };
    return { stdout: join(lines), stderr: '', code: 0 };
  } catch (error) {
    return { stdout: join(current.output), stderr: `${error && error.stack ? error.stack : error}\n`, code: 1 };
  } finally {
    current.output = null;
  }
}

function join(lines) {
  return lines.length ? `${lines.join('\n')}\n` : '';
}

function reset() {
  if (mode === 'wedged' || mode === 'frozen') return;
  mode = 'alive';
  const owner = page;
  schedule(owner, () => load(owner.url));
}

function control(message) {
  switch (message.action) {
    case 'kill':
    case 'wedge':
    case 'freeze':
      mode = { kill: 'killed', wedge: 'wedged', freeze: 'frozen' }[message.action];
      break;
    case 'navigate':
      go(message.url);
      break;
    default:
      throw new Error(`fake webview: unknown control action ${message.action}`);
  }
  return state();
}

function state() {
  return { url: page.url, mode, loads, history: backStack.length + 1 };
}

function handle(message) {
  switch (message.op) {
    case 'eval':
      return answers() ? evaluate(message.source) : null;
    case 'reset':
      reset();
      return { stdout: '', stderr: '', code: 0 };
    case 'control':
      return control(message);
    case 'state':
      return state();
    default:
      throw new Error(`fake webview: unknown op ${message.op}`);
  }
}

load(PAGE_URL);
loads = 0;

const server = net.createServer((connection) => {
  let buffer = '';
  connection.setEncoding('utf8');
  connection.on('data', (chunk) => {
    buffer += chunk;
    const newline = buffer.indexOf('\n');
    if (newline < 0) return;
    const reply = handle(JSON.parse(buffer.slice(0, newline)));
    // A page that does not answer leaves the caller hanging, as evaluateJavaScript does.
    if (reply !== null) connection.end(`${JSON.stringify(reply)}\n`);
  });
  connection.on('error', () => {});
});

fs.rmSync(socketPath, { force: true });
server.listen(socketPath, () => process.stdout.write('ready\n'));
