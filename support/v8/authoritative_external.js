// Loaded by authoritative_external_proof.html. See that file for what this
// proves and how to run it.
if (globalThis !== window ||
    window.window !== window ||
    window.document !== document) {
  throw new Error("bad identity");
}
document.bgColor =
  document.bgColor === "lime" ? "red" : "lime";
