// Shared app state + event bus.
export const bus = new EventTarget();
export function emit(name, detail) {
  bus.dispatchEvent(new CustomEvent(name, {detail}));
}
export function on(name, fn) {
  const h = (e) => fn(e.detail);
  bus.addEventListener(name, h);
  return () => bus.removeEventListener(name, h);
}

export const store = {
  user: null,      // {ldap, display_name, is_admin}
  project: null,   // current Project (when on a project route)
  projects: null,  // cached list for the switcher
  // Constitution editor registers a provider so chat can send draft_md.
  draftProvider: null,  // () => string|null (null when not dirty)
  // Set by chat "Edit then accept"; consumed by the constitution view.
  pendingEdit: null,  // {message_id, constitution_md, change_summary}
  // Remembered so "Review worst-first" etc. can find the latest run.
  lastRunId: {},  // project id → run id
  // Data view selection (for "New run from selection").
  dataSelection: new Set(),
};

export function navigate(path, {replace = false} = {}) {
  emit('navigate', {path, replace});
}
