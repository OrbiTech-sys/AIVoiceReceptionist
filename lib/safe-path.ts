// A "go back to" address from a link or the address bar is only followed if it stays on this site. A leading "//" or "/\"
// would send the visitor to another host (browsers read a backslash as a slash), and anything unusual is refused.
const SAFE_PATH = /^\/(?![/\\])[\w\-./?=&%#:+~@]*$/;

export function isSafePath(value: string): boolean {
  return value.length <= 2000 && SAFE_PATH.test(value) && !value.includes("..");
}
