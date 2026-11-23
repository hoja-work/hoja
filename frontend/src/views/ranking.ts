/**
 * Fractional-index keys for manual order: the browser half of hoja_server/ranking.py.
 *
 * The server computes the key that is stored (under a lock, so two people dropping into the
 * same gap never collide). The browser uses the same algorithm only to show a move at once,
 * before the server answers; the answer then replaces the optimistic key. Both
 * implementations are checked against each other on generated cases (scripts/test-view-filters.mjs).
 */
export const DIGITS =
  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const ZERO = DIGITS[0];
const SMALLEST_INTEGER = "A" + ZERO.repeat(26);

function midpoint(a: string, b: string | null): string {
  if (b !== null && a >= b) throw new Error(`${a} is not below ${b}`);
  if (a.endsWith(ZERO) || (b !== null && b.endsWith(ZERO)))
    throw new Error("a fraction must not end in 0");
  if (b !== null) {
    let n = 0;
    while ((n < a.length ? a[n] : ZERO) === b[n]) n++;
    if (n > 0) return b.slice(0, n) + midpoint(a.slice(n), b.slice(n));
  }
  const digitA = a ? DIGITS.indexOf(a[0]) : 0;
  const digitB = b !== null ? DIGITS.indexOf(b[0]) : DIGITS.length;
  if (digitB - digitA > 1) return DIGITS[Math.round(0.5 * (digitA + digitB))];
  if (b !== null && b.length > 1) return b.slice(0, 1);
  return DIGITS[digitA] + midpoint(a.slice(1), null);
}

function integerLength(head: string): number {
  if (head >= "a" && head <= "z") return head.charCodeAt(0) - 97 + 2;
  if (head >= "A" && head <= "Z") return 90 - head.charCodeAt(0) + 2;
  throw new Error(`invalid order key head ${head}`);
}

function integerPart(key: string): string {
  const n = integerLength(key[0]);
  if (n > key.length) throw new Error(`invalid order key ${key}`);
  return key.slice(0, n);
}

export function validate(key: string): string {
  if (!key) throw new Error("an order key is a non-empty string");
  if (key === SMALLEST_INTEGER) throw new Error("invalid order key");
  for (const ch of key.slice(1))
    if (!DIGITS.includes(ch)) throw new Error(`invalid order key ${key}`);
  const i = integerPart(key);
  if (key.slice(i.length).endsWith(ZERO))
    throw new Error(`invalid order key ${key}`);
  return key;
}

function increment(x: string): string | null {
  const head = x[0];
  const digits = x.slice(1).split("");
  let carry = true;
  for (let i = digits.length - 1; carry && i >= 0; i--) {
    const d = DIGITS.indexOf(digits[i]) + 1;
    if (d === DIGITS.length) digits[i] = ZERO;
    else {
      digits[i] = DIGITS[d];
      carry = false;
    }
  }
  if (!carry) return head + digits.join("");
  if (head === "Z") return "a" + ZERO;
  if (head === "z") return null;
  const next = String.fromCharCode(head.charCodeAt(0) + 1);
  if (next > "a") digits.push(ZERO);
  else digits.pop();
  return next + digits.join("");
}

function decrement(x: string): string | null {
  const head = x[0];
  const digits = x.slice(1).split("");
  let borrow = true;
  for (let i = digits.length - 1; borrow && i >= 0; i--) {
    const d = DIGITS.indexOf(digits[i]) - 1;
    if (d === -1) digits[i] = DIGITS[DIGITS.length - 1];
    else {
      digits[i] = DIGITS[d];
      borrow = false;
    }
  }
  if (!borrow) return head + digits.join("");
  if (head === "a") return "Z" + DIGITS[DIGITS.length - 1];
  if (head === "A") return null;
  const next = String.fromCharCode(head.charCodeAt(0) - 1);
  if (next < "Z") digits.push(DIGITS[DIGITS.length - 1]);
  else digits.pop();
  return next + digits.join("");
}

/** A key strictly between `a` and `b`; null is the open start or end. */
export function keyBetween(a: string | null, b: string | null): string {
  if (a !== null) validate(a);
  if (b !== null) validate(b);
  if (a !== null && b !== null && a >= b)
    throw new Error(`${a} is not below ${b}`);
  if (a === null) {
    if (b === null) return "a" + ZERO;
    const ib = integerPart(b);
    const fb = b.slice(ib.length);
    if (ib === SMALLEST_INTEGER) return ib + midpoint("", fb);
    if (ib < b) return ib;
    const res = decrement(ib);
    if (res === null) throw new Error("cannot decrement any more");
    return res;
  }
  if (b === null) {
    const ia = integerPart(a);
    const fa = a.slice(ia.length);
    const res = increment(ia);
    return res === null ? ia + midpoint(fa, null) : res;
  }
  const ia = integerPart(a);
  const fa = a.slice(ia.length);
  const ib = integerPart(b);
  const fb = b.slice(ib.length);
  if (ia === ib) return ia + midpoint(fa, fb);
  const res = increment(ia);
  if (res === null) throw new Error("cannot increment any more");
  return res < b ? res : ia + midpoint(fa, null);
}

/** Compare two ranks byte-wise (what the database does with COLLATE "C"); empty last. */
export function compareRank(a?: string | null, b?: string | null): number {
  if (!a && !b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return a < b ? -1 : a > b ? 1 : 0;
}
