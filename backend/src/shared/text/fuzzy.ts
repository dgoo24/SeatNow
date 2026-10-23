/**
 * Comparación difusa de textos cortos (nombres de estación).
 * `nearMatch` equivale a Levenshtein(a, b) <= 1, sin matriz completa.
 */
export function nearMatch(a: string, b: string): boolean {
  if (a === b) return true;
  const la = a.length;
  const lb = b.length;
  if (Math.abs(la - lb) > 1) return false;

  let i = 0;
  while (i < la && i < lb && a[i] === b[i]) i++;

  if (la === lb) {
    // Un único carácter distinto.
    for (let j = i + 1; j < la; j++) {
      if (a[j] !== b[j]) return false;
    }
    return true;
  }

  // Inserción o borrado de un carácter.
  const short = la < lb ? a : b;
  const long = la < lb ? b : a;
  for (let j = i; j < short.length; j++) {
    if (short[j] !== long[j + 1]) return false;
  }
  return true;
}
