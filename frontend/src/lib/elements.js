/** Element presentation helpers shared by the tree, the detail pane and the
 *  device overlay, so all three colour and label a node identically. */

/** Canonical role -> CSS custom property. The backend now sends `role`
 *  explicitly, so nothing here has to guess from platform class names. */
export const ROLE_COLORS = {
  button: 'var(--role-button)',
  textbox: 'var(--role-textbox)',
  text: 'var(--role-text)',
  image: 'var(--role-image)',
  switch: 'var(--role-switch)',
  checkbox: 'var(--role-switch)',
};

export function roleColor(role) {
  return ROLE_COLORS[role] || 'var(--role-default)';
}

/** Convert an Appium bounds string to percentages of the device screen, so the
 *  overlay lines up regardless of how the mirror is scaled. */
export function parseBounds(boundsStr, screen) {
  if (!boundsStr) return null;
  const match = boundsStr.match(/\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]/);
  if (!match) return null;

  const [x1, y1, x2, y2] = match.slice(1, 5).map(Number);
  const width = screen?.width || 1080;
  const height = screen?.height || 2400;

  return {
    left: (x1 / width) * 100,
    top: (y1 / height) * 100,
    width: ((x2 - x1) / width) * 100,
    height: ((y2 - y1) / height) * 100,
  };
}

export function elementLabel(node) {
  return node.text || node['content-desc'] || node.id || null;
}
