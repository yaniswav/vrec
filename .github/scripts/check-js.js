"use strict";
// Validates src/vrec/js/*.js: each file is a single JS expression (a bare arrow
// function, an async arrow function, or an IIFE) that vrec reads as text and hands
// to Chrome's DevTools protocol - it is never a standalone script. Plain
// `node --check` mostly happens to accept these anyway (an expression statement can
// start with `(`), so it would not reliably catch every mistake. Instead, wrap each
// file's source as an expression, `return (<source>)`, and pass it to the Function
// constructor: that parses the wrapped code as a real expression and throws a
// SyntaxError immediately if it isn't one, without ever executing the file's body
// (several of these reference `window`, `fetch`, etc. and would throw at runtime
// outside a browser - that's expected and not what this check is about).

const fs = require("fs");
const path = require("path");

const dir = path.join(__dirname, "..", "..", "src", "vrec", "js");
const files = fs.readdirSync(dir).filter((f) => f.endsWith(".js"));

if (files.length === 0) {
  console.error(`No .js files found in ${dir}`);
  process.exit(1);
}

let failed = false;
for (const file of files) {
  const full = path.join(dir, file);
  const src = fs.readFileSync(full, "utf8");
  try {
    // Constructing the Function parses the body eagerly; it is never called.
    new Function(`return (\n${src}\n)`);
    console.log(`OK   ${file}`);
  } catch (err) {
    failed = true;
    console.error(`FAIL ${file}: ${err.message}`);
  }
}

process.exit(failed ? 1 : 0);
