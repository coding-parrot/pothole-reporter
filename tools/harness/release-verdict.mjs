// Historical failures and an empty selection must never produce a green run.
export function releasePassed(results) {
  return results.length > 0 && results.every(result => result.ok === true);
}
