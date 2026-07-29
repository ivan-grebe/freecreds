export function el(tag, attrs = {}, children = []) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") element.className = value;
    else element.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    element.appendChild(
      typeof child === "string" ? document.createTextNode(child) : child,
    );
  }
  return element;
}
