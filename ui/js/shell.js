// Small shared hooks into the app shell, so pages can set the titlebar without importing app.js.

export const shell = { setTitle: () => {}, setSub: () => {} };
export const setTitle = (t, sub = '') => shell.setTitle(t, sub);
export const setSub = (sub) => shell.setSub(sub);
